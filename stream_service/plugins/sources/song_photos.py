"""Song photos: the karaoke song's artist pictures as a scene, framed on the faces and changed with the lyric lines.

Source: Original
Author: smfx (screen-machine)
License: MIT
license_class: permissive

Owner, 26 Nov: "photo processing scenes ... which use publicly-available-from-the-web images of the singer and/or
band ... (and upweight them like we do lyrics effects)".  Flask's karaoke pictures (routes/karaoke/pictures.py) finds,
vets and keeps each artist's pictures; lyric_feed reads the playing song's list (``self.sound.pictures``: a
PictureSet, best first, the song's era first).  This scene shows them; its presets put one of the picture effects on
top (sketchbook, video wall, cube wall, globe, landscape, echo, pop art), which is what makes 1000 px catalogue
pictures look right on a 4K screen.  listens "pictures": the scene director never picks it without at least
director.PICTURES_MIN pictures for the playing song, and upweights it when there are.

* fit: ``cover`` fills the frame, cropped round the faces (the vetting's face boxes; none: the upper third), with the
  faces held at FACE_Y of the height, above the display page's lyrics; ``framed`` shows the whole picture over a
  dark blurred copy of itself, a little above centre.  Album covers are always framed (their words stay whole).
* Each picture drifts (Ken Burns): it zooms in or out by ``zoom`` (alternating) while the view moves towards the
  faces by ``face_focus``.  The next picture fades in over ``transition_s`` and both keep moving.
* When: after the hold (``hold_s`` calm .. ``hold_fast_s`` at energy 1), the next lyric line's start, else the next
  beat (pulse), else HOLD_SLACK_S later.  Which: a shuffled bag, no repeat until all have shown; in a chorus line
  live and group pictures are twice as likely, in a verse portraits.
* Decoding is a daemon thread (PIL, draft-sized, at most LOAD_PX); the render thread uploads at most one picture a
  frame and keeps MAX_SLOTS on the GPU.  No pictures (picked by hand with no song): a dark palette wash.
* ``tint`` washes the picture with the palette (a duotone by luminance); vignette; pulse adds a small brightness
  kick; intensity is the gain.
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

LOAD_PX = 1600           # a picture's longest side on the GPU
BG_W, BG_H = 48, 27      # the framed fit's blurred backdrop
MAX_SLOTS = 4
FACE_Y = 0.36            # the faces' height on screen in the cover fit (the display page's lyrics sit below 0.62)
FACE_MAX = 0.75         # a head (the vetting's boxes include the hair) taller than this share of the screen in the
                         # cover fit: that picture is framed whole
HEAD_ROOM = 0.06         # ... else its face box's top stays at least this far below the top of the screen
FRAME_FILL = 0.88        # the framed fit's picture fills at most this much of the frame
FRAME_Y = 0.45           # ... centred this far down
HOLD_SLACK_S = 2.0       # no line and no beat came: change anyway this long after the hold
NO_FACE = (0.5, 0.3, 0.25)   # a picture with no face box: assume its subject in the upper third
SYNC_DECODE = False      # tools/fxtest: decode on the render thread (short renders have the picture from frame 1)
KIND_FIT = {"chorus": {"live": 2.0, "group": 2.0}, "verse": {"portrait": 2.0}}

# P layout per layer (base k = 0 current, 16 previous): 0 s (image px per out px) | 1 cx | 2 cy (image px) |
# 3 framed (0/1) | 4 image w | 5 image h | 6 frame alpha.   Global from 32: 32..43 palette | 44 period | 45 drift |
# 46 tint | 47 vignette | 48 gain | 49 mix (prev -> cur) | 50 have prev | 51 aspect
_SRC = HELPERS + PALETTE_CUDA + r"""
#define BGW %(bgw)d
#define BGH %(bgh)d
__device__ float3 layer(const uchar4* __restrict__ img, const uchar4* __restrict__ bg, const float* __restrict__ L,
                        float x, float y, int W, int H)
{
    const float s = L[0], cx = L[1], cy = L[2];
    const int iw = (int)L[4], ih = (int)L[5];
    const float ix = cx + (x - 0.5f * W) * s, iy = cy + (y - 0.5f * H) * s;
    float4 c = bilin(img, ix, iy, iw, ih);
    float3 col = make_float3(c.x, c.y, c.z);
    if (L[3] > 0.5f) {                                   // framed: the picture over its own dark blurred copy
        const float4 b = bilin(bg, x / W * BGW, y / H * BGH, BGW, BGH);
        const float ex = fminf(ix, iw - ix) / s, ey = fminf(iy, ih - iy) / s;   // out px to the picture's edge
        const float d = fminf(ex, ey);                    // < 0 outside the picture
        const float a = clamp01(d + 0.5f);
        const float sh = 0.38f * (1.f - (d < 0.f ? 0.6f * clamp01(1.f + d / 28.f) : 0.f));   // a soft drop shadow
        col = make_float3(b.x * sh + (col.x - b.x * sh) * a, b.y * sh + (col.y - b.y * sh) * a,
                          b.z * sh + (col.z - b.z * sh) * a);
    }
    return col;
}

extern "C" __global__
void photos_draw(uchar4* __restrict__ out, const int W, const int H,
                 const uchar4* __restrict__ A, const uchar4* __restrict__ Abg,
                 const uchar4* __restrict__ B, const uchar4* __restrict__ Bbg, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fx = x + 0.5f, fy = y + 0.5f;
    float3 col = layer(A, Abg, P, fx, fy, W, H);
    const float m = P[49];
    if (P[50] > 0.5f && m < 1.f) {
        const float3 o = layer(B, Bbg, P + 16, fx, fy, W, H);
        col = make_float3(o.x + (col.x - o.x) * m, o.y + (col.y - o.y) * m, o.z + (col.z - o.z) * m);
    }
    const float tint = P[46];
    if (tint > 0.f) {
        const float l = (0.2126f * col.x + 0.7152f * col.y + 0.0722f * col.z) / 255.f;
        const float3 pc = pal(l * P[44] + P[45], P);
        const float k = 255.f * (0.2f + 0.95f * l);
        col = make_float3(col.x + (pc.x * k - col.x) * tint, col.y + (pc.y * k - col.y) * tint,
                          col.z + (pc.z * k - col.z) * tint);
    }
    const float u = (fx / W - 0.5f) * P[51], v = fy / H - 0.5f;
    const float r = sqrtf(u * u + v * v);
    const float g = P[48] * (1.f - P[47] * smooth(0.3f, 0.95f, r));
    out[y * W + x] = pack(make_float4(col.x * g, col.y * g, col.z * g, 255.f));
}
""" % {"bgw": BG_W, "bgh": BG_H}
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
    l, t = (small.width - BG_W) // 2, (small.height - BG_H) // 2
    bg = small.crop((l, t, l + BG_W, t + BG_H)).filter(ImageFilter.GaussianBlur(2.5))
    rgba = np.empty((h, w, 4), np.uint8)
    rgba[..., :3] = np.asarray(im)
    rgba[..., 3] = 255
    bga = np.empty((BG_H, BG_W, 4), np.uint8)
    bga[..., :3] = np.asarray(bg)
    bga[..., 3] = 255
    return rgba, bga


class _Loader:
    """Decodes pictures off the render thread, one at a time."""

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


class _Shot:
    """One picture on screen: its framing and its drift."""

    def __init__(self, pic, slot, W: int, H: int, p: dict, zoom_in: bool, framed_fit: bool) -> None:
        self.pic, self.slot = pic, slot
        self.t = 0.0
        ih, iw = slot[0].shape[:2]
        self.iw, self.ih = iw, ih
        fx, fy, fh = pic.face or NO_FACE
        self.face = (fx * iw, fy * ih, fh * ih)
        zoom = float(p["zoom"])
        fs = fh * ih / (H * min(iw / W, ih / H))                 # the face's share of the screen in the cover fit
        self.framed = framed_fit or pic.kind == "cover" or fs * (1.0 + zoom) > FACE_MAX
        z = zoom * (0.4 if self.framed else 1.0)
        self.z0, self.z1 = (1.0, 1.0 + z) if zoom_in else (1.0 + z, 1.0)
        self.face_y = max(FACE_Y, HEAD_ROOM + 0.5 * fs * (1.0 + z))   # where its centre sits on screen
        self.focus = float(p["focus"])
        self.W, self.H = W, H

    def view(self, life: float) -> tuple[float, float, float]:
        """(s, cx, cy) at this point of the shot's life (0..1)."""
        u = min(max(life, 0.0), 1.0)
        u = u * u * (3.0 - 2.0 * u) * 0.6 + u * 0.4               # eased but never still
        z = self.z0 + (self.z1 - self.z0) * u
        W, H, iw, ih = self.W, self.H, self.iw, self.ih
        if self.framed:
            s = max(iw / (W * FRAME_FILL), ih / (H * FRAME_FILL)) / z
            return s, iw * 0.5, ih * 0.5 + (0.5 - FRAME_Y) * H * s
        s = min(iw / W, ih / H) / z
        vw, vh = W * s, H * s
        fx, fy, _ = self.face
        tx, ty = fx, fy + (0.5 - self.face_y) * vh               # the faces at face_y on screen (head room kept)
        k = self.focus * (0.55 + 0.45 * u)                       # drift towards them as it goes
        cx = iw * 0.5 + (tx - iw * 0.5) * k
        cy = ih * 0.42 + (ty - ih * 0.42) * k
        cx = min(max(cx, vw * 0.5), iw - vw * 0.5) if vw < iw else iw * 0.5
        cy = min(max(cy, vh * 0.5), ih - vh * 0.5) if vh < ih else ih * 0.5
        return s, cx, cy


class SongPhotos(Source):
    name = "song_photos"
    label = "Song photos"
    description = ("The karaoke song's artist pictures (found, vetted and kept per artist), framed on the faces, "
                   "drifting, and changed with the lyric lines.")
    listens = ("pictures", "lyrics")
    params = {
        "fit": enum(["cover", "framed"], "cover", "Fit (fill, framed on the faces / whole picture over its blur)",
                    tier="regional", basic=True),
        "hold_s": num(2.0, 30.0, 9.0, "Hold, calm (s)", tier="regional"),
        "hold_fast_s": num(1.0, 12.0, 3.5, "Hold at full energy (s)", tier="regional"),
        "transition_s": num(0.0, 4.0, 1.4, "Cross-fade (s)", tier="regional"),
        "zoom": num(0.0, 0.6, 0.18, "Drift zoom", tier="regional", basic=True),
        "focus": num(0.0, 1.0, 0.75, "Drift towards the faces", tier="regional"),
        "tint": num(0.0, 1.0, 0.0, "Palette wash", tier="regional"),
        "vignette": num(0.0, 1.0, 0.35, "Vignette", tier="regional"),
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
        self.Ph = np.zeros(64, np.float32)
        self.P = cp.zeros(64, cp.float32)
        self.loader = _Loader()
        self.slots: dict[str, tuple] = {}      # picture id -> (rgba, bg) on the GPU
        self.used: dict[str, int] = {}
        self.tick = 0
        self.rng = np.random.default_rng(int(params["seed"]))
        self.set_key: Optional[str] = None
        self.pics: dict[str, object] = {}
        self.order: list[str] = []
        self.bag: list[str] = []
        self.cur: Optional[_Shot] = None
        self.prev: Optional[_Shot] = None
        self.fade = 1.0
        self.next_id: Optional[str] = None
        self.zoom_in = bool(self.rng.integers(2))
        self.last_line = -1
        self.last_pulse = 0.0
        self.drift = 0.0
        self.dark = None

    # -- which picture next
    def _sync(self) -> None:
        ps = getattr(self.sound, "pictures", None) if self.sound is not None else None
        key = ps.key if ps is not None else None
        if key == self.set_key:
            return
        self.set_key = key
        self.pics = {p.id: p for p in (ps.pictures if ps is not None else ())}
        self.order = [p.id for p in (ps.pictures if ps is not None else ())]
        self.bag = [i for i in self.bag if i in self.pics]
        for pid in list(self.slots):
            if pid not in self.pics and (self.cur is None or self.cur.pic.id != pid) and (
                    self.prev is None or self.prev.pic.id != pid):
                self._drop(pid)
        if self.next_id not in self.pics:
            self.next_id = None

    def _section(self) -> Optional[str]:
        ly = getattr(self.sound, "lyrics", None) if self.sound is not None else None
        if ly is None or not getattr(ly, "present", False) or ly.song is None or ly.line < 0:
            return None
        ln = ly.song.lines[ly.line]
        return "chorus" if getattr(ln, "chorus", False) else "verse"

    def _choose(self) -> Optional[str]:
        if not self.order:
            return None
        cur = self.cur.pic.id if self.cur is not None else None
        if not self.bag:
            # the best first time round (the list is best first, the song's era first), shuffled after that
            self.bag = list(self.order) if not self.used else [self.order[i] for i in self.rng.permutation(len(self.order))]
            if len(self.bag) > 1 and self.bag[0] == cur:
                self.bag.append(self.bag.pop(0))
        fit = KIND_FIT.get(self._section() or "", {})
        cands = self.bag[:4]
        w = np.array([fit.get(self.pics[i].kind, 1.0) * (1.0 + 0.5 * self.pics[i].quality) * (0.7 ** k)
                      for k, i in enumerate(cands)], np.float64)
        pid = cands[int(self.rng.choice(len(cands), p=w / w.sum()))]
        self.bag.remove(pid)
        return pid

    # -- GPU slots
    def _drop(self, pid: str) -> None:
        self.slots.pop(pid, None)
        self.used.pop(pid, None)
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
        keep = {s.pic.id for s in (self.cur, self.prev) if s is not None} | {pid}
        while len(self.slots) >= MAX_SLOTS:
            old = min((k for k in self.slots if k not in keep), key=lambda k: self.used.get(k, 0), default=None)
            if old is None:
                break
            self._drop(old)
        rgba, bg = got
        self.slots[pid] = (self.cp.asarray(rgba), self.cp.asarray(bg))
        self.used[pid] = self.tick
        return self.slots[pid]

    # -- render
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        self.tick += 1
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 10.0
        self._sync()

        if self.next_id is None:
            self.next_id = self._choose()
        hold = float(p["hold_s"]) + (float(p["hold_fast_s"]) - float(p["hold_s"])) * e
        trans = max(float(p["transition_s"]), 0.0)
        ly = getattr(self.sound, "lyrics", None) if self.sound is not None else None
        line = ly.line if ly is not None and getattr(ly, "present", False) else -1
        new_line = line >= 0 and line != self.last_line
        self.last_line = line
        beat = pulse > 0.5 and self.last_pulse <= 0.5
        self.last_pulse = pulse
        due = self.cur is None or (self.cur.t >= hold and (new_line or (line < 0 and beat)
                                                           or self.cur.t >= hold + HOLD_SLACK_S))
        if due and self.next_id is not None:
            slot = self._slot(self.next_id)
            if slot is not None:
                self.prev, self.fade = self.cur, 0.0 if self.cur is not None else 1.0
                self.cur = _Shot(self.pics[self.next_id], slot, self.w, self.h, p, self.zoom_in, p["fit"] == "framed")
                self.zoom_in = not self.zoom_in
                self.next_id = self._choose()
        elif self.next_id is not None:
            self._slot(self.next_id)                    # decode and upload ahead

        for s in (self.cur, self.prev):
            if s is not None:
                s.t += dt * (0.6 + 0.4 * spd)
        if self.prev is not None:
            self.fade = 1.0 if trans <= 0.0 else min(1.0, self.fade + dt / trans)
            if self.fade >= 1.0:
                self.prev = None

        dst = out if self.direct else self.buf
        if self.cur is None:
            co = palette_coeffs(p["palette"])
            c = np.clip(np.asarray(co[0:3]) + np.asarray(co[3:6]) * np.cos(2 * np.pi * (np.asarray(co[9:12]) + self.drift)),
                        0, 1) * 0.12 * float(p["intensity"])
            self.ctx.kernels.fill(dst, tuple(int(255 * x) for x in c))
        else:
            life_len = max(hold + trans + HOLD_SLACK_S, 1.0)
            P = self.Ph
            for k, s in ((0, self.cur), (16, self.prev)):
                if s is None:
                    continue
                sc, cx, cy = s.view(s.t / life_len)
                P[k:k + 7] = (sc, cx, cy, 1.0 if s.framed else 0.0, s.iw, s.ih, 1.0)
            P[32:44] = palette_coeffs(p["palette"])
            P[44] = float(p["palette_period"])
            P[45] = self.drift
            P[46] = float(p["tint"])
            P[47] = float(p["vignette"])
            P[48] = float(p["intensity"]) * (1.0 + 0.12 * pulse)
            f = self.fade
            P[49] = f * f * (3.0 - 2.0 * f)
            P[50] = 1.0 if self.prev is not None else 0.0
            P[51] = self.w / self.h
            self.P.set(P)
            a, abg = self.cur.slot
            b, bbg = self.prev.slot if self.prev is not None else (a, abg)
            launch_2d(self.k_draw, self.w, self.h, (dst, np.int32(self.w), np.int32(self.h), a, abg, b, bbg, self.P))
        if not self.direct:
            present(self.ctx, self.buf, out)

    def close(self) -> None:
        try:
            self.loader.stop()
        except Exception:                                # noqa: BLE001
            pass
        self.slots.clear()
        self.cur = self.prev = None
        self.buf = None
