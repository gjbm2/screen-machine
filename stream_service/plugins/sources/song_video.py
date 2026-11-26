"""Song video: the karaoke song's artist live on stage, as moving texture: slowed, blended, drifting, styled.

Source: Original
Author: smfx (screen-machine)
License: MIT
license_class: permissive

Owner, 26 Nov: "live youtube feed would be fun -- but we aren't going to be lip syncing so is more so that we can get
video e.g. only moving surfaces (and as appropriate for the scene in question, probably slowed / styled etc.)".
Flask keeps a few short silent 720p clips of each artist live (routes/karaoke/pictures.py fetch_clips: from their
concert films on YouTube, three frames of each vetted: the artist, fit for children); lyric_feed reads the playing
song's (``self.sound.pictures.clips``).  listens "video": the scene director only picks this when the song has a
clip, and it counts towards the artist's share with the photo looks.

* Decoding: one ffmpeg process per clip on screen (``-stream_loop -1``, scaled and cropped to DEC_W x DEC_H, RGBA on
  a pipe), read by a daemon thread into a small queue; the pipe's back-pressure makes ffmpeg decode only as fast as
  the scene plays, so a slowed clip costs proportionally less.  The render thread uploads at most one new frame
  per clip per frame into a ring of device frames.
* Playback: ``speed`` of real time (0.5: half speed), sped up a little by the energy; between two source frames the
  kernel blends them (smooth slow motion, no stutter).  The picture drifts and pushes in slowly (``zoom``), may be
  mirrored down the middle (``mirror``), washed with the palette (``tint``), given contrast, a beat flash
  (``pulse``) and a vignette.  The presets put a picture effect on top (echo trails, kaleidoscope, VHS, halftone...).
* Changes: every ``bars`` (calm) .. ``bars_fast`` (energy 1) bars, on a beat, another clip (the most engaging
  first) cross-fades in over ``transition_s``, starting at a different point than last time.
* No clips (picked by hand with no song): a dark palette wash.
"""
from __future__ import annotations

import logging
import math
import queue
import subprocess
import threading
import weakref
from typing import Optional

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

log = logging.getLogger("stream_service.song_video")

DEC_W, DEC_H = 1280, 720     # decoded frame size (the clips are 720p)
SRC_FPS = 30.0               # the decoder's output rate (ffmpeg fps filter)
QUEUE_FRAMES = 4
BEAT_GAP_S = 1.6
BEAT_S = 0.5
SYNC_START_S = 0.0           # tools/fxtest: wait this long for a clip's first frames (short renders)

# P: 0..11 palette | 12 period | 13 drift | 14 tint | 15 vignette | 16 gain | 17 aspect | 18 mix A (frame blend)
#  19 mix B | 20 cross-fade A->B | 21 have B | 22 zoom A | 23 pan x A | 24 pan y A | 25 zoom B | 26 pan x B
#  27 pan y B | 28 mirror | 29 contrast | 30 flash
_SRC = HELPERS + PALETTE_CUDA + r"""
#define DW %(dw)d
#define DH %(dh)d
__device__ float3 vid(const uchar4* __restrict__ f0, const uchar4* __restrict__ f1, float m, float u, float v,
                      float z, float px, float py)
{
    const float x = ((u - 0.5f) / z + 0.5f + px) * DW, y = ((v - 0.5f) / z + 0.5f + py) * DH;
    const float4 a = bilin(f0, x, y, DW, DH), b = bilin(f1, x, y, DW, DH);
    return make_float3(a.x + (b.x - a.x) * m, a.y + (b.y - a.y) * m, a.z + (b.z - a.z) * m);
}

extern "C" __global__
void video_draw(uchar4* __restrict__ out, const int W, const int H,
                const uchar4* __restrict__ A0, const uchar4* __restrict__ A1,
                const uchar4* __restrict__ B0, const uchar4* __restrict__ B1, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float u = (x + 0.5f) / W, v = (y + 0.5f) / H;
    if (P[28] > 0.5f && u > 0.5f) u = 1.f - u;              // mirrored down the middle
    float3 col = vid(A0, A1, P[18], u, v, P[22], P[23], P[24]);
    if (P[21] > 0.5f) {
        const float3 b = vid(B0, B1, P[19], u, v, P[25], P[26], P[27]);
        const float k = P[20];
        col = make_float3(col.x + (b.x - col.x) * k, col.y + (b.y - col.y) * k, col.z + (b.z - col.z) * k);
    }
    const float c = P[29];                                    // contrast round mid-grey
    col = make_float3(128.f + (col.x - 128.f) * c, 128.f + (col.y - 128.f) * c, 128.f + (col.z - 128.f) * c);
    const float tint = P[14];
    if (tint > 0.f) {
        const float l = clamp01((0.2126f * col.x + 0.7152f * col.y + 0.0722f * col.z) / 255.f);
        const float3 pc = pal(l * P[12] + P[13], P);
        const float k = 255.f * (0.15f + 1.0f * l);
        col = make_float3(col.x + (pc.x * k - col.x) * tint, col.y + (pc.y * k - col.y) * tint,
                          col.z + (pc.z * k - col.z) * tint);
    }
    const float uu = ((x + 0.5f) / W - 0.5f) * P[17], vv = v - 0.5f;
    const float g = P[16] * (1.f - P[15] * smooth(0.3f, 0.95f, sqrtf(uu * uu + vv * vv))) + P[30];
    out[y * W + x] = pack(make_float4(col.x * g, col.y * g, col.z * g, 255.f));
}
""" % {"dw": DEC_W, "dh": DEC_H}
_OPTIONS = ("--use_fast_math",)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
    except Exception:                                    # noqa: BLE001
        pass


class _Decoder:
    """One clip decoding: ffmpeg on a pipe, frames into a small queue (ffmpeg blocks when it is full)."""

    def __init__(self, path: str, start_s: float, crop: Optional[tuple] = None) -> None:
        self.q: "queue.Queue[np.ndarray]" = queue.Queue(QUEUE_FRAMES)
        self.dead = False
        bars = f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if crop else ""      # baked-in black bars off
        vf = (f"{bars}scale={DEC_W}:{DEC_H}:force_original_aspect_ratio=increase,crop={DEC_W}:{DEC_H},"
              f"fps={SRC_FPS:g}")
        self.proc = subprocess.Popen(
            ["nice", "-n", "10", "ffmpeg", "-v", "error", "-nostdin", "-stream_loop", "-1", "-ss", f"{start_s:.2f}",
             "-i", path, "-an", "-vf", vf, "-pix_fmt", "rgba", "-f", "rawvideo", "-threads", "2", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        weakref.finalize(self, _kill, self.proc)
        self.t = threading.Thread(target=self._run, name="song-video-decode", daemon=True)
        self.t.start()

    def _run(self) -> None:
        n = DEC_W * DEC_H * 4
        out = self.proc.stdout
        try:
            while True:
                buf = bytearray(n)
                view, got = memoryview(buf), 0
                while got < n:
                    k = out.readinto(view[got:])
                    if not k:
                        self.dead = True
                        return
                    got += k
                self.q.put(np.frombuffer(buf, np.uint8).reshape(DEC_H, DEC_W, 4))
        except Exception:                                # noqa: BLE001 - closed under us
            self.dead = True

    def take(self) -> Optional[np.ndarray]:
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        _kill(self.proc)
        try:
            while True:
                self.q.get_nowait()                      # unblock the reader
        except queue.Empty:
            pass


class _Play:
    """One clip on screen: its decoder, its two device frames (the pair being blended) and its play clock."""

    def __init__(self, clip, start_s: float, cp, rng) -> None:
        self.clip = clip
        self.dec = _Decoder(clip.path, start_s, getattr(clip, "crop", None))
        self.f = [cp.zeros((DEC_H, DEC_W, 4), cp.uint8), cp.zeros((DEC_H, DEC_W, 4), cp.uint8)]
        self.have = 0                  # source frames uploaded so far
        self.pos = 0.0                 # play position in source frames
        self.zoom_ph = float(rng.uniform(0, 2 * math.pi))
        self.pan = (float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1)))
        self.t = 0.0

    def ready(self) -> bool:
        return self.have >= 2

    def advance(self, frames: float) -> float:
        """Move the play clock on; upload the next source frame when it is passed (at most one a frame). -> the blend
        between the pair on the device (0: the older, 1: the newer)."""
        if self.have < 2:
            fr = self.dec.take()
            if fr is not None:
                self.f[self.have].set(fr)
                self.have += 1
            return 0.0
        self.pos += frames
        if self.pos >= 1.0:
            fr = self.dec.take()
            if fr is not None:
                self.f[0], self.f[1] = self.f[1], self.f[0]
                self.f[1].set(fr)
                self.pos -= 1.0
            else:
                self.pos = 1.0                           # the decoder is behind: hold on the newer frame
        return min(max(self.pos, 0.0), 1.0)

    def close(self) -> None:
        self.dec.close()


class SongVideo(Source):
    name = "song_video"
    label = "Song video"
    description = ("The karaoke song's artist live on stage (short clips of their concert films, vetted), as moving "
                   "texture: slowed, smoothly blended, drifting and styled; another clip every few bars.")
    listens = ("video",)
    params = {
        "speed_x": num(0.15, 1.0, 0.5, "Playback speed (of real time)", tier="regional", basic=True),
        "bars": num(2.0, 16.0, 8.0, "Bars a clip plays, calm", tier="regional"),
        "bars_fast": num(1.0, 8.0, 4.0, "Bars a clip plays at full energy", tier="regional"),
        "transition_s": num(0.3, 4.0, 1.5, "Cross-fade (s)", tier="regional"),
        "zoom": num(0.0, 0.5, 0.15, "Drift zoom", tier="regional"),
        "mirror": num(0.0, 1.0, 0.0, "Mirror down the middle", tier="regional", integer=True),
        "contrast": num(0.6, 1.8, 1.1, "Contrast", tier="regional"),
        "tint": num(0.0, 1.0, 0.0, "Palette wash", tier="regional", basic=True),
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
        self.k_draw = raw_module(_SRC, _OPTIONS).get_function("video_draw")
        self.Ph = np.zeros(32, np.float32)
        self.P = cp.zeros(32, cp.float32)
        self.rng = np.random.default_rng(int(params["seed"]))
        self.cur: Optional[_Play] = None
        self.nxt: Optional[_Play] = None
        self.fade = 0.0
        self.order: list = []
        self.set_key: Optional[str] = None
        self.k = 0                                       # clips started
        self.starts: dict[str, float] = {}              # clip id -> where it last started (s)
        self.beats = 0.0
        self.t = 0.0
        self.last_beat_t = -9.0
        self.last_pulse = 0.0
        self.drift = 0.0

    def _clips(self) -> list:
        ps = getattr(self.sound, "pictures", None) if self.sound is not None else None
        key = ps.key if ps is not None else None
        if key != self.set_key:
            self.set_key = key
            self.order = list(ps.clips) if ps is not None else []
        return self.order

    def _start(self) -> Optional[_Play]:
        clips = self._clips()
        if not clips:
            return None
        cur = self.cur.clip.id if self.cur is not None else None
        cands = [c for c in clips if c.id != cur] or clips
        c = cands[self.k % len(cands)] if self.k < len(clips) else cands[int(self.rng.integers(len(cands)))]
        self.k += 1
        start = (self.starts.get(c.id, float(self.rng.uniform(0.0, 0.3)) * c.dur) + c.dur * 0.37) % max(c.dur, 1.0)
        self.starts[c.id] = start
        pl = _Play(c, start, self.cp, self.rng)
        if SYNC_START_S > 0:
            import time as _t
            end = _t.monotonic() + SYNC_START_S
            while not pl.ready() and _t.monotonic() < end:
                pl.advance(0.0)
                _t.sleep(0.01)
        return pl

    def _beat(self, pulse: float, dt: float) -> int:
        hit = 0
        if pulse > 0.5 and self.last_pulse <= 0.5:
            hit, self.last_beat_t = 1, self.t
        elif self.t - self.last_beat_t > BEAT_GAP_S and int((self.t - dt) / BEAT_S) != int(self.t / BEAT_S):
            hit = 1
        self.last_pulse = pulse
        self.beats += hit
        return hit

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        self.t += dt
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 10.0
        beat = self._beat(pulse, dt)
        if self.cur is None:
            self.cur = self._start()
        hold = 4.0 * (float(p["bars"]) + (float(p["bars_fast"]) - float(p["bars"])) * e)
        if self.cur is not None and self.nxt is None and self.beats >= hold and beat and len(self._clips()) > 0:
            self.nxt = self._start()
            self.beats = 0.0
        rate = float(p["speed_x"]) * (0.85 + 0.3 * e) * (0.5 + 0.5 * spd) * SRC_FPS
        mA = self.cur.advance(rate * dt) if self.cur is not None else 0.0
        mB = self.nxt.advance(rate * dt) if self.nxt is not None else 0.0
        if self.nxt is not None and self.nxt.ready():
            self.fade = min(1.0, self.fade + dt / max(float(p["transition_s"]), 1e-3))
            if self.fade >= 1.0:
                self.cur.close()
                self.cur, self.nxt, self.fade = self.nxt, None, 0.0
                mA, mB = mB, 0.0
        elif self.nxt is not None and self.nxt.dec.dead and not self.nxt.ready():
            self.nxt.close()
            self.nxt = None

        dst = out if self.direct else self.buf
        if self.cur is None or not self.cur.ready():
            co = palette_coeffs(p["palette"])
            c = np.clip(np.asarray(co[0:3]) + np.asarray(co[3:6]) * np.cos(2 * np.pi * (np.asarray(co[9:12]) + self.drift)),
                        0, 1) * 0.1 * float(p["intensity"])
            self.ctx.kernels.fill(dst, tuple(int(255 * x) for x in c))
        else:
            P = self.Ph
            P[0:12] = palette_coeffs(p["palette"])
            P[12] = float(p["palette_period"])
            P[13] = self.drift
            P[14] = float(p["tint"])
            P[15] = float(p["vignette"])
            P[16] = float(p["intensity"])
            P[17] = self.w / self.h
            P[18], P[19] = mA, mB
            P[20] = self.fade * self.fade * (3.0 - 2.0 * self.fade)
            P[21] = 1.0 if self.nxt is not None and self.nxt.ready() else 0.0
            z = float(p["zoom"])
            for base, pl in ((22, self.cur), (25, self.nxt)):
                if pl is None:
                    continue
                pl.t += dt * (0.5 + 0.5 * spd)
                zz = 1.0 + z * (0.5 + 0.5 * math.sin(pl.zoom_ph + pl.t * 0.11))
                room = 0.5 * (1.0 - 1.0 / zz)
                P[base] = zz
                P[base + 1] = room * 0.9 * math.sin(pl.t * 0.07 + pl.pan[0] * 3.0)
                P[base + 2] = room * 0.9 * math.sin(pl.t * 0.05 + pl.pan[1] * 3.0)
            P[28] = float(p["mirror"])
            P[29] = float(p["contrast"])
            P[30] = 0.12 * pulse * pulse
            self.P.set(P)
            a0, a1 = self.cur.f
            b0, b1 = self.nxt.f if self.nxt is not None and self.nxt.ready() else (a0, a1)
            launch_2d(self.k_draw, self.w, self.h, (dst, np.int32(self.w), np.int32(self.h), a0, a1, b0, b1, self.P))
        if not self.direct:
            present(self.ctx, self.buf, out)

    def close(self) -> None:
        for pl in (self.cur, self.nxt):
            if pl is not None:
                pl.close()
        self.cur = self.nxt = None
        self.buf = None
