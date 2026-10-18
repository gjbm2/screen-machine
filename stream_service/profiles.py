"""Encoder profiles (LIVE_V1 §1.1).

Colour tags: the renderer's frames are sRGB-encoded and converted with the BT.709 matrix to limited range,
so by default the stream is tagged primaries bt709, matrix bt709, range tv, transfer iec61966-2-1 (sRGB):
a browser then shows the decoded R'G'B' values as sRGB, like the <img> of the same still.  ``color_trc`` is
per profile, and a session may override it (``"color_trc": "bt709"`` in POST /api/sessions) for the
day-1 colour check (AV_PROGRAMME §3.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

# ffmpeg name -> ISO/IEC 23091-2 code point (for the MP4 'colr' nclx box)
TRC_CODES = {"bt709": 1, "iec61966-2-1": 13, "gamma22": 4, "smpte170m": 6}
PRIMARIES_CODES = {"bt709": 1}
MATRIX_CODES = {"bt709": 1}


@dataclass(frozen=True)
class Profile:
    name: str
    codec: str          # h264 | hevc | av1
    w: int
    h: int
    fps: int
    bitrate: str
    gop_s: float = 1.0
    preset: str = "p1"  # NVENC preset (p1 fastest .. p7 best)
    # NVENC output delay in frames.  0 (the lab's choice) makes ffmpeg wait for each packet before reading the
    # next frame: at 4K that caps the pipe+encode input at ~66 fps (P4000), too close to 60.  2 lets ffmpeg
    # read ahead while NVENC works (~94 fps) for +2 frames (33 ms) of latency.  See README "Performance".
    nvenc_delay: int = 0
    color_primaries: str = "bt709"
    color_trc: str = "iec61966-2-1"
    colorspace: str = "bt709"
    color_range: str = "tv"
    target_buffer_ms: int = 300
    extra: tuple[str, ...] = field(default=())
    # Rate-control buffer in seconds of the bitrate (None: 2 s on the GPU path, ffmpeg's own default on the pipe) and
    # an HEVC level held at Main tier ("5.1"): a 2 s buffer at 40M puts 4K60 in High tier, and without a fixed level
    # NVENC re-derives it after an fps Reconfigure (4K30 at 40M: High tier 5.0).  Measured on the P4000, 18 Oct.
    vbv_s: Optional[float] = None
    level: Optional[str] = None

    def encoder_args(self) -> list[str]:
        """Extra ffmpeg output args: VUI colour tags (written into the SPS / sequence header), the buffer and level."""
        out = ["-color_primaries", self.color_primaries, "-color_trc", self.color_trc,
               "-colorspace", self.colorspace, "-color_range", self.color_range]
        if self.vbv_s:
            from .encode.encoder import _parse_rate
            out += ["-bufsize", str(int(_parse_rate(self.bitrate) * self.vbv_s))]
        if self.level:
            out += ["-level", self.level, "-tier", "main"]
        return [*out, *self.extra]

    def colr(self) -> tuple[int, int, int, bool]:
        """(primaries, transfer, matrix, full_range) for the init segment's colr/nclx box."""
        return (PRIMARIES_CODES.get(self.color_primaries, 2), TRC_CODES.get(self.color_trc, 2),
                MATRIX_CODES.get(self.colorspace, 2), self.color_range == "pc")

    def describe(self) -> dict:
        return {"codec": self.codec, "w": self.w, "h": self.h, "fps": self.fps, "bitrate": self.bitrate,
                "gop_s": self.gop_s,
                # additive (not in LIVE_V1's example): how the stream is tagged
                "color": {"primaries": self.color_primaries, "trc": self.color_trc,
                          "matrix": self.colorspace, "range": self.color_range},
                "target_buffer_ms": self.target_buffer_ms, "nvenc_delay_frames": self.nvenc_delay,
                "vbv_s": self.vbv_s, "level": self.level}

    def with_trc(self, trc: Optional[str]) -> "Profile":
        if not trc or trc == self.color_trc:
            return self
        if trc not in TRC_CODES:
            raise ValueError(f"color_trc must be one of {sorted(TRC_CODES)}")
        return replace(self, color_trc=trc)


# Spatial AQ at strength 12 is what removes the blocking in smooth gradients (NVENC sweep, 14 Oct:
# flat-region PSNR 49 -> 57 dB at 4K60 on both GPUs, +2 dB overall, ~1.5-3 ms extra encode, no extra frames).
# tune/lookahead/temporal AQ/multipass made no difference; a short VBV starves the 1 s IDRs, so the
# default 2 s VBV stays.
AQ = ("-spatial-aq", "1", "-aq-strength", "12")

# The pages' buffer target for the screens' profiles (owner 17 Oct: sound to glass under 0.5 s, push hard).  The lab
# ran 4K60 to both TVs at 67 ms and found ~110 ms "few stalls" (experiments/stream-latency/README.md); 120 ms is that
# plus a little.  The page hard-jumps to the live edge when it gets 70 ms past this (2f).
LIVE_BUFFER_MS = 120

PROFILES: dict[str, Profile] = {p.name: p for p in (
    # 4K60 (plan change 5): 40M CBR with a 1 s buffer is HEVC Main tier level 5.1 (its 40 Mbit/s limit); 50M is not
    Profile("hevc-2160p60", "hevc", 3840, 2160, 60, "40M", preset="p4", nvenc_delay=2, extra=AQ,
            target_buffer_ms=LIVE_BUFFER_MS, vbv_s=1.0, level="5.1"),
    # nvenc_delay 1 (+33 ms): ffmpeg reads the next frame while NVENC works; interim until GPU-resident encode (W1.6)
    Profile("hevc-2160p30", "hevc", 3840, 2160, 30, "30M", preset="p5", nvenc_delay=1, extra=AQ,
            target_buffer_ms=LIVE_BUFFER_MS),
    Profile("hevc-1080p60", "hevc", 1920, 1080, 60, "16M", preset="p5", extra=AQ, target_buffer_ms=LIVE_BUFFER_MS),
    Profile("hevc-1080p30", "hevc", 1920, 1080, 30, "10M", preset="p5", extra=AQ,    # devtest: a busy browser
            target_buffer_ms=LIVE_BUFFER_MS),
    Profile("h264-720p30", "h264", 1280, 720, 30, "6M", preset="p5", extra=AQ),
    Profile("av1-720p30", "av1", 1280, 720, 30, "3M"),
)}

DEFAULT_PROFILE = "hevc-2160p60"


def at_fps(p: Profile, fps: int) -> Profile:
    """The same picture at another frame rate (stream_switches.json ``fps``): the sibling profile of that rate
    (hevc-2160p30 <-> hevc-2160p60: its bitrate, preset, delay, buffer and level) with this one's size and colour.
    Itself when it has that rate already or no sibling (h264-720p30)."""
    if p.fps == fps:
        return p
    base, _, _ = p.name.rpartition("p")
    sib = PROFILES.get(f"{base}p{fps}")
    if sib is None or sib.codec != p.codec:
        return p
    return replace(sib, w=p.w, h=p.h, color_trc=p.color_trc)
