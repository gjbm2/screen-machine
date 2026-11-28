"""Song photos: the karaoke song's artist photos, always moving: drifting, floating, scrolling, flipping.

Source: Original
Author: smfx (screen-machine)
License: MIT
license_class: permissive

Owner, 26 Nov: "photo processing scenes ... which use publicly-available-from-the-web images of the singer and/or
band"; "it's VERY rare that a STATIC photo of the artist is going to keep a viewer's attention ... they need to
crossfade, flow, move, animate".  Flask finds, vets and keeps each artist's photos (routes/karaoke/pictures.py);
lyric_feed reads the playing song's list (``self.sound.pictures``, the most engaging first).  listens "pictures": the
scene director only picks this with enough engaging photos (director.PICTURES_MIN), then gives it its share.

Everything on screen moves all the time.  One kernel draws up to MAX_LAYERS photo PANELS (each: a rotated,
rounded, shadowed rectangle on screen showing a crop of a photo) over a backdrop (a blurred, slowly zooming copy of
one photo).  The ``layout`` decides the panels:

  * ``drift``: one photo fills the screen, pushing in and out (``zoom``) and panning towards the faces (held at
    FACE_Y, above the display page's lyrics) with a slight turn; the beat surges the push (a rate kick).  On the bar
    (``bars`` calm .. ``bars_fast`` at energy 1; a lyric line start or a beat lands it) the next photo comes in: a
    dissolve when calm, else a push (slides in) or a zoom-through, both photos moving.  A close portrait that would
    be all head (FACE_MAX) floats framed over its own blur instead.
  * ``float``: three or four photos as prints at different sizes and depths, drifting across (smaller = further =
    slower), turning a little; the beat nudges them on; a new one enters as one leaves.
  * ``strip``: a film strip above the lyrics, scrolling right to left at a speed set by the energy (the beat kicks
    it), each frame pushing in slowly.
  * ``mosaic``: a grid of photos each pushing in within its cell; a card flips over to a new photo every few beats.

Which photo: a shuffled bag, the most engaging first and most often (appeal), performing and band shots before
portraits (more so in a chorus), never one already on screen when another will do.  Decoding is a daemon thread
(PIL, at most LOAD_PX); the render thread uploads at most one photo a frame and keeps MAX_SLOTS on the GPU.  No
photos: a dark palette wash.  ``tint`` washes them with the palette; vignette; intensity is the gain.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
from typing import Optional

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

log = logging.getLogger("stream_service.song_photos")

LOAD_PX = 1600           # a photo's longest side on the GPU
BG_W, BG_H = 48, 27      # the backdrop's blurred copy
MAX_SLOTS = 10           # photos on the GPU at once
MAX_LAYERS = 8           # panels drawn at once
LW = 16                  # floats per panel
FACE_Y = 0.36            # the faces' height on screen in a full-screen photo (the display page's lyrics sit below 0.62)
FACE_MAX = 0.75          # a head (the vetting's boxes include the hair) taller than this share of the screen at full
                         # screen: that photo floats framed over its blur instead
NO_FACE = (0.5, 0.3, 0.25)   # a photo with no face box: its subject in the upper third
BEAT_GAP_S = 1.6         # no beat for this long: the clock runs on time (BEAT_S a beat)
BEAT_S = 0.5
SYNC_DECODE = False      # tools/fxtest: decode on the render thread (short renders have the photo from frame 1)
KIND_WEIGHT = {"live": 2.5, "group": 2.0, "portrait": 1.0}   # performing first (owner, 26 Nov)
KIND_FIT = {"chorus": {"live": 1.6, "group": 1.6}, "verse": {"portrait": 1.5}}

# panel floats: 0 slot | 1 cx | 2 cy | 3 half w | 4 half h (screen px) | 5 cos | 6 sin | 7 s (image px per panel px)
#  8 image cx | 9 image cy | 10 alpha | 11 corner radius px | 12 shadow | 13 flip (x scale of the image, 0..1)
# P: 0..11 palette | 12 period | 13 drift | 14 tint | 15 vignette | 16 gain | 17 aspect | 18 backdrop slot (-1: none)
#  19 backdrop zoom | 20 backdrop level | 21 layers
_SRC = HELPERS + PALETTE_CUDA + r"""
#define BGW %(bgw)d
#define BGH %(bgh)d
#define LW %(lw)d
extern "C" __global__
void photos_draw(uchar4* __restrict__ out, const int W, const int H,
                 const unsigned long long* __restrict__ tex, const unsigned long long* __restrict__ bgs,
                 const int* __restrict__ dims, const float* __restrict__ L, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fx = x + 0.5f, fy = y + 0.5f;
    float3 col;
    const int bs = (int)P[18];
    if (bs >= 0) {
        const uchar4* bg = (const uchar4*)bgs[bs];
        const float z = P[19];
        const float4 b = bilin(bg, ((fx / W - 0.5f) / z + 0.5f) * BGW, ((fy / H - 0.5f) / z + 0.5f) * BGH, BGW, BGH);
        col = make_float3(b.x * P[20], b.y * P[20], b.z * P[20]);
    } else {
        const float3 pc = pal(P[13] + 0.3f * fy / H, P);
        col = make_float3(pc.x * 22.f, pc.y * 22.f, pc.z * 22.f);
    }
    const int n = (int)P[21];
    for (int l = 0; l < n; ++l) {
        const float* Q = L + l * LW;
        const float alpha = Q[10];
        if (alpha <= 0.f) continue;
        const float dx = fx - Q[1], dy = fy - Q[2];
        const float lx = dx * Q[5] + dy * Q[6], ly = -dx * Q[6] + dy * Q[5];
        const float hw = Q[3] * fmaxf(Q[13], 1e-3f), hh = Q[4], r = fminf(Q[11], fminf(hw, hh));
        const float qx = fabsf(lx) - hw + r, qy = fabsf(ly) - hh + r;
        const float d = sqrtf(fmaxf(qx, 0.f) * fmaxf(qx, 0.f) + fmaxf(qy, 0.f) * fmaxf(qy, 0.f))
                        + fminf(fmaxf(qx, qy), 0.f) - r;                 // < 0 inside the panel
        if (Q[12] > 0.f && d > -2.f) {                                    // a soft shadow round it
            const float sd = fmaxf(d - 0.012f * H, 0.f);
            const float sh = Q[12] * alpha * __expf(-sd / (0.03f * H)) * clamp01(d + 2.f);
            col = make_float3(col.x * (1.f - sh), col.y * (1.f - sh), col.z * (1.f - sh));
        }
        const float a = clamp01(0.5f - d) * alpha;
        if (a <= 0.f) continue;
        const int sl = (int)Q[0];
        const int iw = dims[2 * sl], ih = dims[2 * sl + 1];
        const float flip = fmaxf(Q[13], 1e-3f);
        const float4 c = bilin((const uchar4*)tex[sl], Q[8] + lx / flip * Q[7], Q[9] + ly * Q[7], iw, ih);
        col = make_float3(col.x + (c.x - col.x) * a, col.y + (c.y - col.y) * a, col.z + (c.z - col.z) * a);
    }
    const float tint = P[14];
    if (tint > 0.f) {
        const float l = (0.2126f * col.x + 0.7152f * col.y + 0.0722f * col.z) / 255.f;
        const float3 pc = pal(l * P[12] + P[13], P);
        const float k = 255.f * (0.2f + 0.95f * l);
        col = make_float3(col.x + (pc.x * k - col.x) * tint, col.y + (pc.y * k - col.y) * tint,
                          col.z + (pc.z * k - col.z) * tint);
    }
    const float u = (fx / W - 0.5f) * P[17], v = fy / H - 0.5f;
    const float g = P[16] * (1.f - P[15] * smooth(0.3f, 0.95f, sqrtf(u * u + v * v)));
    out[y * W + x] = pack(make_float4(col.x * g, col.y * g, col.z * g, 255.f));
}
""" % {"bgw": BG_W, "bgh": BG_H, "lw": LW}
_OPTIONS = ("--use_fast_math",)


def _decode(path: str) -> tuple[np.ndarray, np.ndarray]:
    """-> (RGBA uint8 at most LOAD_PX, its BG_W x BG_H blurred cover crop)."""
    from PIL import Image, ImageFilter
    im = Image.open(path)
    im.draft("RGB", (LOAD_PX, LOAD_PX))
    im = im.convert("RGB")
    im.thumbnail((LOAD_PX, LOAD_PX), Image.LANCZOS)
    w, h = im.size
    k = max(BG_W / w, BG_H / h)
    small = im.resize((max(BG_W, round(w * k)), max(BG_H, round(h * k))), Image.BILINEAR)
    left, top = (small.width - BG_W) // 2, (small.height - BG_H) // 2
    bg = small.crop((left, top, left + BG_W, top + BG_H)).filter(ImageFilter.GaussianBlur(2.5))
    rgba = np.empty((h, w, 4), np.uint8)
    rgba[..., :3] = np.asarray(im)
    rgba[..., 3] = 255
    bga = np.empty((BG_H, BG_W, 4), np.uint8)
    bga[..., :3] = np.asarray(bg)
    bga[..., 3] = 255
    return rgba, bga


class _Loader:
    """Decodes photos off the render thread, one at a time."""

    def __init__(self) -> None:
        self.q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self.done: dict[str, tuple] = {}
        self.failed: set[str] = set()
        self.wanted: set[str] = set()
        self.lock = threading.Lock()
        self.t = threading.Thread(target=self._run, name="song-photos-decode", daemon=True)
        self.t.start()

    def want(self, pid: str, path: str) -> None:
        with self.lock:
            if pid in self.wanted or pid in self.failed:
                return
            self.wanted.add(pid)
        self.q.put((pid, path))

    def take(self, pid: str) -> Optional[tuple]:
        with self.lock:
            return self.done.pop(pid, None)

    def forget(self, pid: str) -> None:
        with self.lock:
            self.wanted.discard(pid)
            self.done.pop(pid, None)

    def stop(self) -> None:
        self.q.put(None)

    def _run(self) -> None:
        while True:
            job = self.q.get()
            if job is None:
                return
            pid, path = job
            try:
                got = _decode(path)
            except Exception as e:                       # noqa: BLE001 - a broken file: never again
                log.info("song photos: %s didn't decode (%s)", pid, type(e).__name__)
                with self.lock:
                    self.failed.add(pid)
                    self.wanted.discard(pid)
                continue
            with self.lock:
                if pid in self.wanted:
                    self.done[pid] = got


def frame_view(iw: float, ih: float, pw: float, ph: float, face: Optional[tuple], z: float, focus: float,
               face_y: float = FACE_Y) -> tuple[float, float, float]:
    """A photo (iw x ih) filling a panel (pw x ph) at zoom ``z`` (1: just covering), moved ``focus`` of the way to
    hold its faces at ``face_y`` of the panel's height -> (s: image px per panel px, image cx, image cy).  Never
    shows past the photo's edge."""
    s = min(iw / pw, ih / ph) / max(z, 1e-3)
    vw, vh = pw * s, ph * s
    fx, fy, _ = face or NO_FACE
    tx, ty = fx * iw, fy * ih + (0.5 - face_y) * vh
    cx = iw * 0.5 + (tx - iw * 0.5) * focus
    cy = ih * 0.42 + (ty - ih * 0.42) * focus
    cx = min(max(cx, vw * 0.5), iw - vw * 0.5) if vw < iw else iw * 0.5
    cy = min(max(cy, vh * 0.5), ih - vh * 0.5) if vh < ih else ih * 0.5
    return s, cx, cy


def ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


class _Panel:
    """One photo on screen: where it is, how it moves, its own push-in."""

    def __init__(self, pid: str, pic, rng) -> None:
        self.pid, self.pic = pid, pic
        self.t = 0.0                 # its own clock (s)
        self.push = 0.0              # its push-in phase (rate-kicked by the beat; in and out, never still)
        self.zin = bool(rng.integers(2))
        self.turn = float(rng.uniform(-1.0, 1.0))
        self.x = self.y = 0.0        # screen centre (px)
        self.w = self.h = 0.0        # size (px)
        self.vx = self.vy = 0.0
        self.rot = 0.0
        self.alpha = 1.0
        self.flip = 1.0
        self.shadow = 0.0
        self.radius = 0.0
        self.depth = 1.0
        self.enter = 1.0
        self.how = "dissolve"
        self.side = 1
        self.flipping = None


class SongPhotos(Source):
    name = "song_photos"
    label = "Song photos"
    description = ("The karaoke song's artist photos (found, vetted and kept per artist), always moving: a drifting "
                   "push-in, floating prints, a film strip or a flipping mosaic, on the beat.")
    listens = ("pictures", "lyrics")
    params = {
        "layout": enum(["drift", "float", "strip", "mosaic"], "drift",
                       "Layout (one drifting photo / floating prints / film strip / mosaic)", tier="regional",
                       basic=True),
        "bars": num(1.0, 8.0, 2.0, "Bars a photo holds, calm", tier="regional"),
        "bars_fast": num(0.5, 4.0, 1.0, "Bars a photo holds at full energy", tier="regional"),
        "transition_s": num(0.2, 3.0, 1.0, "Change (s)", tier="regional"),
        "zoom": num(0.0, 0.8, 0.35, "Push-in", tier="regional", basic=True),
        "focus": num(0.0, 1.0, 0.75, "Towards the faces", tier="regional"),
        "motion": num(0.2, 3.0, 1.0, "Motion", tier="regional", basic=True),
        "tint": num(0.0, 1.0, 0.0, "Palette wash", tier="regional"),
        "vignette": num(0.0, 1.0, 0.3, "Vignette", tier="regional"),
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.cp = cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.direct else cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k_draw = raw_module(_SRC, _OPTIONS).get_function("photos_draw")
        self.Ph = np.zeros(32, np.float32)
        self.P = cp.zeros(32, cp.float32)
        self.Lh = np.zeros((MAX_LAYERS, LW), np.float32)
        self.L = cp.zeros((MAX_LAYERS, LW), cp.float32)
        self.texh = np.zeros(MAX_SLOTS, np.uint64)
        self.bgh = np.zeros(MAX_SLOTS, np.uint64)
        self.dimh = np.zeros(2 * MAX_SLOTS, np.int32)
        self.tex = cp.zeros(MAX_SLOTS, cp.uint64)
        self.bgp = cp.zeros(MAX_SLOTS, cp.uint64)
        self.dims = cp.zeros(2 * MAX_SLOTS, cp.int32)
        self.loader = _Loader()
        self.slots: dict[str, tuple] = {}      # photo id -> (rgba, bg) on the GPU
        self.slot_of: dict[str, int] = {}      # photo id -> its index in the pointer tables
        self.used: dict[str, int] = {}
        self.tick = 0
        self.rng = np.random.default_rng(int(params["seed"]))
        self.set_key: Optional[str] = None
        self.pics: dict[str, object] = {}
        self.order: list[str] = []
        self.bag: list[str] = []
        self.shown = 0
        self._want: Optional[str] = None
        self.panels: list[_Panel] = []
        self.leaving: list[tuple[_Panel, str, float]] = []   # (panel, how, progress) on its way out
        self.layout = params["layout"]
        self.beats = 0.0                       # beats since the last change (the beat, else a time clock)
        self.last_beat_t = -9.0
        self.t = 0.0
        self.last_line = -1
        self.last_pulse = 0.0
        self.clock_beat: Optional[int] = None  # the beat clock's whole beat last frame (None: no sure clock)
        self.on_bar = False                    # this frame's beat is a downbeat (only with a sure clock)
        self.drift = 0.0
        self.spawn_in = 0.0
        self.bg_pid: Optional[str] = None
        self.bg_t = 0.0

    # -- which photo next
    def _sync(self) -> None:
        ps = getattr(self.sound, "pictures", None) if self.sound is not None else None
        key = ps.key if ps is not None else None
        if key == self.set_key:
            return
        self.set_key = key
        self.pics = {p.id: p for p in (ps.pictures if ps is not None else ())}
        self.order = [p.id for p in (ps.pictures if ps is not None else ())]
        self.bag = [i for i in self.bag if i in self.pics]
        on = {p.pid for p in self.panels} | {p.pid for p, _, _ in self.leaving}
        for pid in list(self.slots):
            if pid not in self.pics and pid not in on:
                self._drop(pid)

    def _section(self) -> Optional[str]:
        ly = getattr(self.sound, "lyrics", None) if self.sound is not None else None
        if ly is None or not getattr(ly, "present", False) or ly.song is None or ly.line < 0:
            return None
        ln = ly.song.lines[ly.line]
        return "chorus" if getattr(ln, "chorus", False) else "verse"

    def _choose(self, avoid: set) -> Optional[str]:
        if not self.order:
            return None
        if not self.bag:
            # the most engaging first time round (the list is), shuffled after that
            self.bag = (list(self.order) if not self.shown
                        else [self.order[i] for i in self.rng.permutation(len(self.order))])
        fit = KIND_FIT.get(self._section() or "", {})
        cands = [i for i in self.bag if i not in avoid][:5] or self.bag[:5]
        w = np.array([KIND_WEIGHT.get(self.pics[i].kind, 0.5) * fit.get(self.pics[i].kind, 1.0)
                      * (0.15 + getattr(self.pics[i], "appeal", 0.5)) ** 2 * (0.75 ** k)
                      for k, i in enumerate(cands)], np.float64)
        pid = cands[int(self.rng.choice(len(cands), p=w / w.sum()))]
        self.bag.remove(pid)
        self.shown += 1
        return pid

    def _next(self) -> Optional[tuple[str, object]]:
        """The next photo, if it is on the GPU (else asked for, and None this frame)."""
        if self._want is None or self._want not in self.pics:
            self._want = self._choose({p.pid for p in self.panels})
        if self._want is None or self._slot(self._want) is None:
            return None
        pid, self._want = self._want, None
        return pid, self.pics[pid]

    # -- GPU slots
    def _drop(self, pid: str) -> None:
        self.slots.pop(pid, None)
        self.used.pop(pid, None)
        self.slot_of.pop(pid, None)
        self.loader.forget(pid)

    def _slot(self, pid: str) -> Optional[tuple]:
        if pid in self.slots:
            self.used[pid] = self.tick
            return self.slots[pid]
        got = self.loader.take(pid)
        if got is None and SYNC_DECODE and pid in self.pics:
            got = _decode(self.pics[pid].path)
        if got is None:
            p = self.pics.get(pid)
            if p is not None:
                self.loader.want(pid, p.path)
            return None
        keep = {p.pid for p in self.panels} | {p.pid for p, _, _ in self.leaving} | {pid, self.bg_pid}
        while len(self.slots) >= MAX_SLOTS:
            old = min((k for k in self.slots if k not in keep), key=lambda k: self.used.get(k, 0), default=None)
            if old is None:
                return None
            self._drop(old)
        rgba, bg = got
        self.slots[pid] = (self.cp.asarray(rgba), self.cp.asarray(bg))
        self.slot_of[pid] = sorted(set(range(MAX_SLOTS)) - set(self.slot_of.values()))[0]
        self.used[pid] = self.tick
        return self.slots[pid]

    def _size(self, pid: str) -> tuple[int, int]:
        a = self.slots[pid][0]
        return a.shape[1], a.shape[0]

    # -- the beat
    def _clock(self):
        """show_service's sure beat clock at photon time (effects.beat_clock), else None."""
        try:
            from stream_service.effects import beat_clock
            bc = beat_clock(getattr(getattr(self, "modulation", None), "runtime", None))
            return bc if bc is not None and bc.source == "show" and bc.bpm > 0.0 else None
        except Exception:
            return None

    def _beat(self, pulse: float, dt: float) -> int:
        """1 on a beat, counted into self.beats: the beat clock's own beats while it is sure (27 Nov: the pulse's
        threshold missed most of the shallow ladder's beats, so photos changed on a time clock, off the music), with
        ``on_bar`` on its downbeats; else the pulse; else a time beat."""
        bc = self._clock()
        self.on_bar = False
        if bc is not None:
            k = math.floor(bc.beats)
            hit = 1 if self.clock_beat is not None and k != self.clock_beat else 0
            self.clock_beat = k
            if hit:
                self.last_beat_t = self.t
                self.on_bar = (k - round(bc.bar_beat)) % max(1, int(bc.beats_per_bar)) == 0
            self.last_pulse = pulse
            self.beats += hit
            return hit
        self.clock_beat = None
        hit = 0
        if pulse > 0.5 and self.last_pulse <= 0.5:
            hit, self.last_beat_t = 1, self.t
        elif self.t - self.last_beat_t > BEAT_GAP_S and int((self.t - dt) / BEAT_S) != int(self.t / BEAT_S):
            hit = 1
        self.last_pulse = pulse
        self.beats += hit
        return hit

    def _hold_beats(self, p: dict, e: float) -> float:
        return 4.0 * (float(p["bars"]) + (float(p["bars_fast"]) - float(p["bars"])) * e)

    # -- layouts
    def _full(self, pn: _Panel) -> None:
        """A full-screen panel (a close portrait floats framed over its blur instead)."""
        W, H = self.w, self.h
        iw, ih = self._size(pn.pid)
        _, _, fh = pn.pic.face or NO_FACE
        if fh * ih / (H * min(iw / W, ih / H)) * 1.3 > FACE_MAX:
            k = min(0.86 * W / iw, 0.86 * H / ih)
            pn.w, pn.h, pn.x, pn.y = iw * k, ih * k, W * 0.5, H * 0.45
            pn.shadow, pn.radius = 0.55, 0.012 * H
        else:
            pn.w, pn.h, pn.x, pn.y = W * 1.08, H * 1.08, W * 0.5, H * 0.5   # overscan: room to turn
            pn.shadow, pn.radius = 0.0, 0.0

    def _drift(self, p: dict, e: float, beat: int, dt: float, line_start: bool) -> None:
        trans = max(float(p["transition_s"]), 1e-3)
        hold = self._hold_beats(p, e)
        on = beat and (self.on_bar or self.clock_beat is None or self.beats >= hold + 4.0)   # a sure clock: the bar
        due = not self.panels or (self.beats >= hold and (on or line_start))
        if due:
            got = self._next()
            if got is not None:
                pn = _Panel(got[0], got[1], self.rng)
                self._full(pn)
                if self.panels:
                    old = self.panels.pop()
                    if e < 0.4:
                        how = "dissolve"
                    else:
                        how = "push" if self._section() == "chorus" or self.rng.random() < 0.5 else "zoom"
                    self.leaving.append((old, how, 0.0))
                    pn.how, pn.side, pn.enter = how, (1 if self.rng.random() < 0.5 else -1), 0.0
                self.panels.append(pn)
                self.beats = 0.0
        for pn in self.panels:
            pn.enter = min(1.0, pn.enter + dt / trans)
        self.leaving = [(o, how, min(1.0, k + dt / trans)) for o, how, k in self.leaving if k < 1.0]

    def _float(self, e: float, dt: float, motion: float, pulse: float) -> None:
        """Prints flowing one way across (this instance's direction), the screen always full: the first ones spread
        across it, then a new one enters as soon as the last one in has come a fair way."""
        W, H = self.w, self.h
        n = 4 if e < 0.55 else 5
        if not hasattr(self, "_flow"):
            self._flow = 1 if self.rng.random() < 0.5 else -1
        d = self._flow
        last = self.panels[-1] if self.panels else None
        spacing = W / n
        need = len(self.panels) < n + 1 and (
            last is None or abs(getattr(self, "_last_x", 0.0) - (-last.w if d > 0 else W + last.w)) > spacing * 0.9
            or not getattr(self, "_floated", False))
        if need:
            got = self._next()
            if got is not None:
                pn = _Panel(got[0], got[1], self.rng)
                iw, ih = self._size(pn.pid)
                pn.depth = float(self.rng.uniform(0.0, 1.0))
                ph = H * (0.34 + 0.24 * pn.depth)
                pn.w, pn.h = ph * min(max(iw / ih, 0.7), 1.6), ph
                if not getattr(self, "_floated", False):
                    k = self._placing = getattr(self, "_placing", 0) + 1
                    pn.x = W * (k - 0.5) / n + float(self.rng.uniform(-0.04, 0.04)) * W
                    self._floated = k >= n
                else:
                    pn.x = -pn.w * 0.55 if d > 0 else W + pn.w * 0.55
                pn.y = H * float(self.rng.uniform(0.24, 0.5))
                pn.vx = d * W / (13.0 - 4.0 * pn.depth)
                pn.vy = float(self.rng.uniform(-0.012, 0.012)) * H
                pn.rot = math.radians(float(self.rng.uniform(-7, 7)))
                pn.shadow, pn.radius = 0.6, 0.012 * H
                pn.born = True
                self.panels.append(pn)
                self._newest = pn
        sp = (0.6 + 0.8 * e) * motion * (1.0 + 1.2 * pulse)      # the beat nudges them on (a rate kick)
        for pn in self.panels:
            pn.x += pn.vx * sp * dt
            pn.y += pn.vy * sp * dt
            pn.rot += math.radians(1.2) * pn.turn * dt * motion
        nw = getattr(self, "_newest", None)
        if nw is not None:
            self._last_x = nw.x
        self.panels = [pn for pn in self.panels if -pn.w < pn.x < W + pn.w]

    def _strip(self, e: float, dt: float, motion: float, pulse: float) -> None:
        W, H = self.w, self.h
        ph, gap = H * 0.6, W * 0.025
        speed = W / 16.0 * (0.5 + 1.0 * e) * motion * (1.0 + 1.2 * pulse)
        for pn in self.panels:
            pn.x -= speed * dt
        self.panels = [pn for pn in self.panels if pn.x + pn.w * 0.5 > -gap]
        right = max((pn.x + pn.w * 0.5 for pn in self.panels), default=W * 0.15)
        while right < W + ph:
            got = self._next()
            if got is None:
                break
            pn = _Panel(got[0], got[1], self.rng)
            iw, ih = self._size(pn.pid)
            pn.w, pn.h = ph * min(max(iw / ih, 0.7), 1.5), ph
            pn.x, pn.y = right + gap + pn.w * 0.5, H * 0.38
            pn.shadow, pn.radius = 0.5, 0.01 * H
            self.panels.append(pn)
            right = pn.x + pn.w * 0.5

    def _mosaic(self, p: dict, e: float, beat: int, dt: float) -> None:
        W, H = self.w, self.h
        cols, rows = 3, 2
        gap = 0.012 * W
        cw, ch = (W - gap * (cols + 1)) / cols, (H - gap * (rows + 1)) / rows
        if len(self.panels) < cols * rows:
            got = self._next()
            if got is not None:
                k = len(self.panels)
                pn = _Panel(got[0], got[1], self.rng)
                pn.w, pn.h = cw, ch
                pn.x = gap + cw * 0.5 + (k % cols) * (cw + gap)
                pn.y = gap + ch * 0.5 + (k // cols) * (ch + gap)
                pn.radius, pn.shadow = 0.01 * H, 0.3
                self.panels.append(pn)
            return
        every = max(2.0, self._hold_beats(p, e) / 4.0)
        if self.beats >= every and beat and not any(pn.flipping for pn in self.panels):
            got = self._next()
            if got is not None:
                self.panels[int(self.rng.integers(len(self.panels)))].flipping = (got, 0.0)
                self.beats = 0.0
        for i, pn in enumerate(self.panels):
            if pn.flipping is None:
                continue
            (pid, pic), u = pn.flipping
            u += dt / 0.5
            if u >= 0.5 and pn.pid != pid:                      # edge-on: the new photo
                nn = _Panel(pid, pic, self.rng)
                nn.w, nn.h, nn.x, nn.y, nn.radius, nn.shadow = pn.w, pn.h, pn.x, pn.y, pn.radius, pn.shadow
                self.panels[i] = pn = nn
            pn.flip = abs(math.cos(math.pi * min(u, 1.0)))
            pn.flipping = None if u >= 1.0 else ((pid, pic), u)
            if u >= 1.0:
                pn.flip = 1.0

    # -- the panels into the kernel's table
    def _layer(self, row: np.ndarray, pn: _Panel, p: dict, alpha: float, dx: float = 0.0, scale: float = 1.0,
               zoom_extra: float = 0.0) -> None:
        iw, ih = self._size(pn.pid)
        w, h = pn.w * scale, pn.h * scale
        z = float(p["zoom"]) * (0.5 if pn.shadow > 0 else 1.0)
        u = pn.push % 2.0                                       # 0..2: in, then back out (never still)
        u = ease(u if u <= 1.0 else 2.0 - u)
        u = u if pn.zin else 1.0 - u
        s, cx, cy = frame_view(iw, ih, w, h, pn.pic.face, 1.0 + z * u + zoom_extra, float(p["focus"]),
                               FACE_Y if pn.shadow == 0 else 0.4)
        rot = pn.rot + (math.radians(1.2) * pn.turn * math.sin(pn.t * 0.21) if pn.shadow == 0 else 0.0)
        row[:] = (self.slot_of[pn.pid], pn.x + dx, pn.y, w * 0.5, h * 0.5, math.cos(rot), math.sin(rot), s, cx, cy,
                  alpha * pn.alpha, pn.radius, pn.shadow, pn.flip, 0.0, 0.0)

    def _rows(self, pulse: float) -> list[tuple]:
        """(panel, alpha, x offset, scale, extra zoom) back to front."""
        W = self.w
        if self.layout != "drift":
            order = sorted(self.panels, key=lambda q: q.depth) if self.layout == "float" else self.panels
            return [(pn, 1.0, 0.0, 1.0 + 0.03 * pulse * (pn.shadow > 0), 0.0) for pn in order]
        rows = []
        side = self.panels[-1].side if self.panels else 1
        for o, how, k in self.leaving:
            kk = ease(k)
            if how == "push":
                rows.append((o, 1.0, -side * W * kk, 1.0, 0.0))
            elif how == "zoom":
                rows.append((o, 1.0 - kk, 0.0, 1.0, 0.6 * kk))       # it rushes past as it fades
            else:
                rows.append((o, 1.0, 0.0, 1.0, 0.0))
        for pn in self.panels:
            kk = ease(pn.enter)
            if pn.how == "push" and kk < 1.0:
                rows.append((pn, 1.0, pn.side * W * (1.0 - kk), 1.0, 0.0))
            elif pn.how == "zoom" and kk < 1.0:
                rows.append((pn, kk, 0.0, 1.0, -0.25 * (1.0 - kk)))
            else:
                rows.append((pn, kk if pn.how == "dissolve" else 1.0, 0.0, 1.0, 0.0))
        return rows

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        self.tick += 1
        self.t += dt
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        motion = float(p["motion"]) * (0.4 + 0.6 * spd)
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 10.0
        self._sync()
        ly = getattr(self.sound, "lyrics", None) if self.sound is not None else None
        line = ly.line if ly is not None and getattr(ly, "present", False) else -1
        line_start = line >= 0 and line != self.last_line
        self.last_line = line
        beat = self._beat(pulse, dt)
        if self.layout == "float":
            self._float(e, dt, motion, pulse)
        elif self.layout == "strip":
            self._strip(e, dt, motion, pulse)
        elif self.layout == "mosaic":
            self._mosaic(p, e, beat, dt)
        else:
            self._drift(p, e, beat, dt, line_start)
        if not self.panels:
            self._next()                                       # ask for the first photo anyway
        rate = (0.12 + 0.1 * e) * motion * (1.0 + 2.5 * pulse)  # the push-in, surging on the beat
        for pn in self.panels + [o for o, _, _ in self.leaving]:
            pn.t += dt * motion
            pn.push += rate * dt

        rows = [r for r in self._rows(pulse) if r[0].pid in self.slot_of][-MAX_LAYERS:]
        top = self.panels[-1] if self.panels else None
        if top is not None and top.pid in self.slot_of:
            self.bg_pid = top.pid
        P = self.Ph
        P[0:12] = palette_coeffs(p["palette"])
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = float(p["tint"])
        P[15] = float(p["vignette"])
        P[16] = float(p["intensity"]) * (1.0 + 0.08 * pulse)
        P[17] = self.w / self.h
        P[18] = self.slot_of.get(self.bg_pid, -1) if self.bg_pid else -1
        self.bg_t += dt * motion
        P[19] = 1.15 + 0.1 * math.sin(self.bg_t * 0.13)
        P[20] = 0.38 if self.layout != "mosaic" else 0.15
        P[21] = len(rows)
        for i, (pn, alpha, dx, sc, zx) in enumerate(rows):
            self._layer(self.Lh[i], pn, p, alpha, dx, sc, zx)
        for pid, i in self.slot_of.items():
            rgba, bg = self.slots[pid]
            self.texh[i], self.bgh[i] = rgba.data.ptr, bg.data.ptr
            self.dimh[2 * i], self.dimh[2 * i + 1] = rgba.shape[1], rgba.shape[0]
        self.tex.set(self.texh)
        self.bgp.set(self.bgh)
        self.dims.set(self.dimh)
        self.L.set(self.Lh)
        self.P.set(P)
        dst = out if self.direct else self.buf
        launch_2d(self.k_draw, self.w, self.h, (dst, np.int32(self.w), np.int32(self.h), self.tex, self.bgp, self.dims,
                                                self.L, self.P))
        if not self.direct:
            present(self.ctx, self.buf, out)

    def close(self) -> None:
        try:
            self.loader.stop()
        except Exception:                                # noqa: BLE001
            pass
        self.slots.clear()
        self.panels = []
        self.leaving = []
        self.buf = None
