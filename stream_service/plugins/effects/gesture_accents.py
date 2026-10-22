"""Gesture accents: the room's musical moments on this screen, as light added on the beat (docs/ENSEMBLE.md §8.2).

Source: Original
Author: screen-machine (original)
License: MIT (original)
license_class: permissive

The renderer's half of the room ensemble (owner, 22 Oct: "something might happen on south (left) and then on north
(right) and/or echo between").  show_service sends each moment once as room.gesture with a part per player;
stream_service/gestures.py keeps the parts whose player is this screen (``ctx.screen`` through the ensemble's
``screens``) and the timing rule.  Each part is scheduled on the frame it is first seen, so its peak lands on the
glass at its at_ms (the session's render->photon lead, from ``session_runtime``, which the EffectSlot sets): already
late, at once with a squeezed attack; more than a beat late, not shown.  A re-sent part that has not begun moves; one
already playing plays out as it began, even if called off (light never snaps away).

Every kind is one or more looks from config/live/gesture_accents.yaml (hot-reloaded):
  flash   light over the picture (white mixed with the picture's own colour, or the part's palette colour), even or
          centred;
  swell   a bloom-like lift, strongest on the brights, in each pixel's own colour brightened;
  ring    a ring of light spreading from the part's peak, at the centre or placed by register (low notes left, high
          right), with an optional trailing ring and a little refraction of the picture under it;
  shake   a small decaying tap of the whole picture (edges clamped: never a black border);
and a part with a colour (a palette role word, placed on the session palette by ``roles``) adds a short palette pop.
The envelope is the gesture's shape: a rise up to the peak, hold, then exp decay; a swell rises over its dur_ms.
``level`` x the part's strength (final from show).  Everything is screen-blended (out = c + (255 - c) x add, add >= 0):
light is only ever added, so a gesture never dims a screen or takes it to black.

GPU, per frame: the host steps the accents (scalars plus at most 32 ring rows of 12 floats, uploaded only while a
ring shows) and one full-size kernel does the rest: the rings' glow and refraction (each ring culled to its band),
the shake and refraction displacement (one bilinear tap), then the flash, swell and ring light.  Nothing in play, or
amount 0: a copy (out == src exactly).

internal: the slot belongs to the ensemble (gestures.sync_slots puts it last on the FX bus); it is not in the Library,
the presets or the FX director's pool.
"""
from __future__ import annotations

import math
import time
import zlib
from typing import Any, Optional

import numpy as np

from stream_service import gestures
from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num

MAXR = 32                      # ring rows on the GPU
RW = 12                        # floats per ring row
LOOKS = ("flash", "swell", "ring", "shake")
REG_MARGIN = 0.08              # register placement: A0 at 8 % of the width, C8 at 92 % (as note_ripples)
R0 = 0.005                     # a ring's radius at its peak (frame heights)
TRAIL_R = 0.7                  # a trailing ring's radius, x the leading one's
TRAIL_W = 0.8                  # ... and its width
REFRACT_K = 1.5                # refract 1 bends the picture by up to ~1.3 x the ring's width
MIN_W = 0.002                  # the thinnest ring (frame heights)
EPS = 1e-4

# row: 0 cx | 1 cy (frame heights) | 2 radius | 3 1/width | 4 amplitude | 5..7 rgb | 8 refraction (height units)
#      9 own colour (1: white + the picture's own colour; 0: rgb) | 10 outer cull radius | 11 inner cull radius
_SRC = HELPERS + f"""
#define MAXR {MAXR}
#define RW {RW}
""" + r"""
extern "C" __global__
void ga_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
             const float* __restrict__ rows, const int n,
             const float er, const float eg, const float eb, const float eo,
             const float kr, const float kg, const float kb, const float ko,
             const float sr, const float sg, const float sb, const float so,
             const float own, const float shx, const float shy, const float amt)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 c0 = ld(src, x, y, W, H);
    const float inv = 1.f / (float)H;
    const float px = (x + 0.5f) * inv, py = (y + 0.5f) * inv;
    float m = fmaxf(fmaxf(c0.x, c0.y), fmaxf(c0.z, 51.f));
    const float o0x = c0.x / m, o0y = c0.y / m, o0z = c0.z / m;   // the picture's own colour, brightened (0..1)
    float gr = 0.f, gg = 0.f, gb = 0.f, dx = 0.f, dy = 0.f;
    const int nn = min(max(n, 0), MAXR);
    for (int i = 0; i < nn; ++i) {                                 // bounded: at most MAXR rings
        const float* r = rows + i * RW;
        const float ex = px - r[0], ey = py - r[1];
        const float d2 = ex * ex + ey * ey;
        if (d2 > r[10] * r[10] || d2 < r[11] * r[11]) continue;  // outside the ring's band
        const float d = sqrtf(d2) + 1e-6f;
        const float t = (d - r[2]) * r[3];
        const float a = r[4] * __expf(-t * t);
        if (r[9] > 0.5f) {
            const float w = 1.f - own;
            gr += a * (w + own * o0x); gg += a * (w + own * o0y); gb += a * (w + own * o0z);
        } else {
            gr += a * r[5]; gg += a * r[6]; gb += a * r[7];
        }
        const float s = r[8] * (-2.f * t) * a / d;                // the ring's slope bends the picture
        dx += ex * s; dy += ey * s;
    }
    float4 c = c0;
    const float ox = dx * H * amt + shx, oy = dy * H * amt + shy;  // pixels
    if (fabsf(ox) + fabsf(oy) > 0.01f) c = bilin(src, x + 0.5f + ox, y + 0.5f + oy, W, H);
    m = fmaxf(fmaxf(c.x, c.y), fmaxf(c.z, 51.f));
    const float qx = c.x / m, qy = c.y / m, qz = c.z / m;
    const float ux = (x + 0.5f) / (float)W - 0.5f, uy = (y + 0.5f) / (float)H - 0.5f;
    const float fall = clamp01(1.f - 2.f * (ux * ux + uy * uy));  // centred: 1 in the middle, 0 in the corners
    const float hl = 0.2f + 0.8f * smooth(0.15f, 0.9f, luma(c));  // a swell lifts the brights most
    const float ar = clamp01(amt * (er + eo * qx + fall * (kr + ko * qx) + hl * (sr + so * qx) + gr));
    const float ag = clamp01(amt * (eg + eo * qy + fall * (kg + ko * qy) + hl * (sg + so * qy) + gg));
    const float ab = clamp01(amt * (eb + eo * qz + fall * (kb + ko * qz) + hl * (sb + so * qz) + gb));
    out[y * W + x] = pack(make_float4(c.x + (255.f - c.x) * ar, c.y + (255.f - c.y) * ag,
                                      c.z + (255.f - c.z) * ab, 255.f));
}
"""


def _clamp01(v: float) -> float:
    return 0.0 if v <= 0.0 else 1.0 if v >= 1.0 else v


class _Acc:
    """One look of one part, on the render clock."""
    __slots__ = ("look", "spec", "level", "rgb", "start", "peak", "hold", "decay", "reg", "ang")

    def __init__(self, look: str, spec: dict, level: float, rgb: Optional[np.ndarray], start: float, peak: float,
                 hold: float, decay: float, reg: Optional[float], ang: float):
        self.look, self.spec, self.level, self.rgb = look, spec, level, rgb
        self.start, self.peak, self.hold, self.decay, self.reg, self.ang = start, peak, hold, decay, reg, ang


class GestureAccents(Effect):
    name = "gesture_accents"
    stage = "overlay"
    label = "Room gestures"
    description = ("The room's musical moments on this screen, on the beat: flashes, swells, spreading rings, note "
                   "ripples placed by register and small taps, as the room's interplay sends them here.")
    internal = True
    default_bindings: dict = {}
    session_runtime: Any = None        # modulation.SessionRuntime this frame (EffectSlot): frame time, lead, palette
    params = {
        "amount": num(0.0, 1.0, 1.0, "Amount", tier="realtime", bind="level"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self._k = mod.get_function("ga_comp")
        self.rows = cp.zeros(MAXR * RW, cp.float32)
        self.rows_h = np.zeros((MAXR, RW), np.float32)
        self._parts: dict = {}         # (gesture id, gen) -> [at_s, start, [_Acc]]
        self._rev = -1
        self.dropped = 0               # parts more than a beat late (read by tests / diagnostics)

    # ---------------------------------------------------------------------------------------------------- host side
    def _clock(self) -> tuple[float, float, Any]:
        rt = self.session_runtime
        now = float(getattr(rt, "now", 0.0) or 0.0) if rt is not None else 0.0
        if now <= 0.0:
            now = time.monotonic()
        sp = getattr(rt, "sampler", None) if rt is not None else None
        lead = float(getattr(sp, "lead", 0.0) or 0.0) if sp is not None else 0.0
        return now, lead, getattr(rt, "palette_rows", None) if rt is not None else None

    @staticmethod
    def _looks(kind: Any, cfg: dict) -> list:
        k = (cfg.get("kinds") or {}).get(kind) if isinstance(cfg.get("kinds"), dict) else None
        specs = [k] if isinstance(k, dict) else k if isinstance(k, list) else []
        return [s for s in specs if isinstance(s, dict) and s.get("look") in LOOKS]

    @staticmethod
    def _role_rgb(word: Any, cfg: dict, pal: Any) -> Optional[np.ndarray]:
        """A palette role word -> its colour on the session palette, brightened; None: the light's own colour."""
        roles = cfg.get("roles") if isinstance(cfg.get("roles"), dict) else {}
        pos = roles.get(word) if isinstance(word, str) else None
        if pal is None or isinstance(pos, bool) or not isinstance(pos, (int, float)):
            return None
        P = np.asarray(pal, np.float64).reshape(-1)
        if P.size < 12 or not np.all(np.isfinite(P[:12])):
            return None
        col = np.clip(P[0:3] + P[3:6] * np.cos(2.0 * math.pi * (P[6:9] * float(pos) + P[9:12])), 0.0, 1.0)
        return col / max(float(col.max()), 0.25)

    def _take(self, now: float, lead: float, cfg: dict, pal: Any) -> None:
        """New and moved parts for this screen, scheduled now; parts called off or moved before they began, gone."""
        book = gestures.BOOK
        if book.rev == self._rev:
            return
        rev, parts = book.parts(getattr(self.ctx, "screen", None))
        self._rev = rev
        bc = self.beat_clock
        try:
            beat_s = 60.0 / float(bc.bpm) if bc is not None and float(bc.bpm) > 0.0 else None
        except (TypeError, ValueError, AttributeError):
            beat_s = None
        if beat_s is None:
            beat_s = gestures.num(cfg, "no_grid_beat_s")
        ease = gestures.num(cfg, "late_attack_ms") / 1000.0
        pop = cfg.get("colour") if isinstance(cfg.get("colour"), dict) else {}
        seen = set()
        for gid, g, part in parts:
            try:
                at_s = float(part["at_ms"]) / 1000.0
                strength = _clamp01(float(part.get("strength") or 0.0))
                gen = int(part.get("gen") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(at_s):
                continue
            key = (gid, gen)
            seen.add(key)
            old = self._parts.get(key)
            if old is not None and (old[0] == at_s or old[1] <= now):
                continue                                # unchanged, or already playing: it plays out as it began
            shape = g.get("shape") if isinstance(g.get("shape"), dict) else {}
            attack = gestures.num(shape, "attack_ms", 0.0) / 1000.0
            hold = max(0.0, gestures.num(shape, "hold_ms", 0.0) / 1000.0)
            decay = max(0.0, gestures.num(shape, "decay_ms", 0.0) / 1000.0)
            dur = max(0.0, gestures.num(g, "dur_ms", 0.0) / 1000.0)
            when = gestures.schedule(at_s, now, lead, attack, beat_s, ease, dur)
            if when is None:                            # more than a beat late: off the beat, not shown
                self.dropped += 1
                self._parts.pop(key, None)
                continue
            start, peak = when
            reg = g.get("register")
            reg = _clamp01(float(reg)) if isinstance(reg, (int, float)) and not isinstance(reg, bool) else None
            rgb = self._role_rgb(part.get("colour"), cfg, pal)
            ang = (zlib.crc32(f"{gid}:{gen}".encode()) % 3600) / 3600.0 * 2.0 * math.pi
            accs = []
            for spec in self._looks(g.get("kind"), cfg):
                lv = gestures.num(spec, "level", 0.0) * strength
                if lv > 0.0:
                    accs.append(_Acc(spec["look"], spec, lv, rgb, start, peak, hold, decay, reg, ang))
            if rgb is not None:                         # a part with a colour: a short palette pop
                lv = gestures.num(pop, "level", 0.0) * strength
                if lv > 0.0:
                    accs.append(_Acc("flash", {}, lv, rgb, start, peak, 0.0,
                                     max(0.0, gestures.num(pop, "decay_ms", 0.0) / 1000.0), reg, ang))
            self._parts[key] = [at_s, start, accs]
        for key in [k for k, v in self._parts.items() if k not in seen and v[1] > now]:
            del self._parts[key]                        # called off, or moved away, before it began
        cap = max(1, int(gestures.num(cfg, "max_accents")))
        if len(self._parts) > cap:                      # the oldest go first
            for key in sorted(self._parts, key=lambda k: self._parts[k][1])[:len(self._parts) - cap]:
                del self._parts[key]

    def _pack(self, now: float, cfg: dict) -> tuple:
        """This frame's light: (ring rows, even flash, centred flash, swell (each rgb + own), shake px, px)."""
        own = _clamp01(gestures.num(cfg, "own_colour"))
        white = 1.0 - own
        E, K, S = np.zeros(4), np.zeros(4), np.zeros(4)
        sx = sy = 0.0
        W, H = self.ctx.width, self.ctx.height
        A = W / float(H)
        rows: list = []
        gone = []
        for key, (_, _, accs) in self._parts.items():
            live = False
            for a in accs:
                if not gestures.ended(now, a.peak, a.hold, a.decay):
                    live = True
                w = gestures.weight(now, a.start, a.peak, a.hold, a.decay)
                if w <= EPS:
                    continue
                v = a.level * w
                if a.rgb is None:
                    col, o = (white, white, white), own
                else:
                    col, o = a.rgb, 0.0
                sp = a.spec
                if a.look == "flash":
                    k = _clamp01(gestures.num(sp, "centre", 0.0))
                    E[:3] += np.multiply(col, v * (1.0 - k))
                    E[3] += o * v * (1.0 - k)
                    K[:3] += np.multiply(col, v * k)
                    K[3] += o * v * k
                elif a.look == "swell":
                    S[:3] += np.multiply(col, v)
                    S[3] += o * v
                elif a.look == "shake":
                    hz = max(0.0, gestures.num(sp, "hz", 0.0))
                    off = v * H * math.sin(2.0 * math.pi * hz * (now - a.start))
                    sx += off * math.cos(a.ang)
                    sy += off * math.sin(a.ang)
                else:                                   # ring
                    size = max(MIN_W, gestures.num(sp, "size", 0.0))
                    grow = max(0.01, gestures.num(sp, "grow_ms", 0.0) / 1000.0)
                    R = R0 + size * (1.0 - math.exp(-max(0.0, now - a.peak) / grow))
                    wd = max(MIN_W, gestures.num(sp, "width", 0.0)) * (0.6 + 0.8 * min(1.0, R / size))
                    cx = A * (REG_MARGIN + (1.0 - 2.0 * REG_MARGIN) * a.reg) \
                        if sp.get("x") == "register" and a.reg is not None else A * 0.5
                    cy = gestures.num(sp, "y", 0.5)
                    refr = _clamp01(gestures.num(sp, "refract", 0.0)) * REFRACT_K * wd
                    rows.append((v, cx, cy, R, wd, col, a.rgb is None, refr))
                    tr = _clamp01(gestures.num(sp, "trail", 0.0))
                    if tr > 0.0 and R * TRAIL_R > wd:
                        rows.append((v * tr, cx, cy, R * TRAIL_R, wd * TRAIL_W, col, a.rgb is None, refr * tr))
            if not live:
                gone.append(key)
        for key in gone:
            del self._parts[key]
        if len(rows) > MAXR:                            # the brightest rings
            rows.sort(key=lambda r: -r[0])
            rows = rows[:MAXR]
        h = self.rows_h
        for i, (v, cx, cy, R, wd, col, mine, refr) in enumerate(rows):
            h[i] = (cx, cy, R, 1.0 / wd, v, col[0], col[1], col[2], refr, 1.0 if mine else 0.0, R + 3.0 * wd,
                    max(0.0, R - 3.0 * wd))
        return len(rows), np.minimum(E, 1.0), np.minimum(K, 1.0), np.minimum(S, 1.0), sx, sy

    # ---------------------------------------------------------------------------------------------------- render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        amount = _clamp01(float(p.get("amount", 1.0)))
        cfg = gestures.CONFIG.get()
        now, lead, pal = self._clock()
        self._take(now, lead, cfg, pal)
        if not self._parts:
            cp.copyto(out, src)
            return
        n, E, K, S, sx, sy = self._pack(now, cfg)
        if amount <= EPS or (n == 0 and abs(sx) + abs(sy) < 0.01 and not (E.any() or K.any() or S.any())):
            cp.copyto(out, src)
            return
        if n:
            self.rows.set(self.rows_h.reshape(-1))
        W, H = self.ctx.width, self.ctx.height
        f = np.float32
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self.rows, np.int32(n),
                                  *(f(v) for v in E), *(f(v) for v in K), *(f(v) for v in S),
                                  f(_clamp01(gestures.num(cfg, "own_colour"))), f(sx * amount), f(sy * amount),
                                  f(amount)))

    def close(self) -> None:
        self.rows = None
        self._parts = {}
