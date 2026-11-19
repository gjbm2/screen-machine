"""A screen clip's slow, eased play, rendered into the file (owner, 19 Nov: "the ease-in and ease-out actually
matters ... if that's not smooth enough, produce longer mp4s with the ease-in/out rendered into the mp4 and play them
at full speed").

The browser can't do it: slowing a clip with playbackRate only holds each frame longer (at 0.065x a 24 fps frame
stays up for 0.6 s), so the start and end of the old loop stepped. Here every output frame is placed on an eased
curve: the clip starts from rest, rises over `ramp_s` to a steady pace and comes back to rest over another `ramp_s`.
A position that falls between two of the worker's (already 3x interpolated) frames is a blend of the two, which for
the slow, subtle motion these clips have is a true in-between. The last `settle_s` fade into frame 0, so the clip
ends on exactly the picture it starts on, and the TV can rest on it and play again with no jump.

A catalogue entry with `loop` ({"play_s", "ramp_s", "settle_s", "rest_s": [shortest, longest]}) gets this; the
result's `clip` metadata (published in output/<screen>.mp4.json) tells the TV page to play it at 1x and rest between
plays for a random time: the more of the picture moves, the longer the rest (owner: "30 s to 3 m depending on the
scale of the movement").
"""
import json
import math
import os
import subprocess
import tempfile

import numpy as np

FPS = 30            # an even cadence on the TVs' 60 Hz
SMALL, LARGE = 3.0, 12.0  # mean change against frame 0 (0-255) of a barely moving clip and of a busy one (mist over a whole loch)


def position(t, play, ramp):
    """0..1 along the source at time t: velocity rises from 0 on a half cosine over `ramp`, holds, and falls back."""
    ramp = min(ramp, play / 2)
    total = play - ramp

    def ramped(x):  # distance covered x seconds into a ramp, at a top speed of 1
        return x / 2 - ramp / (2 * math.pi) * math.sin(math.pi * x / ramp) if ramp else x

    if t <= 0:
        return 0.0
    if t >= play:
        return 1.0
    if t < ramp:
        d = ramped(t)
    elif t <= play - ramp:
        d = ramp / 2 + (t - ramp)
    else:
        d = total - ramped(play - t)
    return d / total


def rest_range(motion, rest_s):
    """The TV's rest between plays: from `rest_s[0]` to ~half way for a barely moving clip, up to `rest_s[1]` for a busy one."""
    lo, hi = rest_s
    k = min(1.0, max(0.0, (motion - SMALL) / (LARGE - SMALL)))
    start = lo + k * (hi - lo) / 2
    return [round(start), round(min(hi, start + (hi - lo) * (0.3 + 0.2 * k)))]


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=width,height,nb_read_packets", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"]), int(s["nb_read_packets"])


def _blocks(f):
    """8x8 block means: the picture as the eye takes it in, without the grain (motion is measured on this)."""
    h, w = f.shape[0] // 8 * 8, f.shape[1] // 8 * 8
    return f[:h, :w].reshape(h // 8, 8, w // 8, 8, 3).mean(axis=(1, 3), dtype=np.float32)


def ease_file(src, dst, play_s=30.0, ramp_s=8.0, settle_s=3.0):
    """Render src (any clip) into dst on the eased curve; returns how much of the picture moves (mean |frame - frame 0|)."""
    w, h, n = _probe(src)
    size = w * h * 3
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium",
                            "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", dst],
                           stdin=subprocess.PIPE)
    frames, read, motion, ref = {}, -1, 0.0, None

    def frame(i):  # source frames arrive in order and positions never go back: keep the two in use (and frame 0)
        nonlocal read, motion, ref
        i = min(i, n - 1)
        while read < i:
            buf = dec.stdout.read(size)
            if len(buf) < size:          # fewer frames than the container said: hold the last one
                return frames[read]
            read += 1
            f = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            frames[read] = f
            small = _blocks(f)
            if read:
                motion = max(motion, float(np.abs(small - ref).mean()))
            else:
                ref = small
            for k in [k for k in frames if 0 < k < read - 1]:
                del frames[k]
        return frames[i]

    try:
        first = frame(0).astype(np.float32)
        total = int(round(play_s * FPS))
        for k in range(total + 1):
            t = k / FPS
            x = position(t, play_s, ramp_s) * (n - 1)
            i = int(x)
            a = frame(i).astype(np.float32)
            frac = x - i
            out = a if frac < 1e-3 else a + (frame(i + 1).astype(np.float32) - a) * frac
            if t > play_s - settle_s:
                u = (t - (play_s - settle_s)) / settle_s
                wgt = u * u * (3 - 2 * u)
                out = out + (first - out) * wgt
            enc.stdin.write(np.clip(out + 0.5, 0, 255).astype(np.uint8).tobytes())
        frame(n - 1)                     # read on to the end, so the motion counts the whole clip
    finally:
        enc.stdin.close()
        dec.stdout.close()
        dec.wait()
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg could not write the eased clip")
    return motion


def ease_result(source, loop, job_id):
    """A finished job's clip (URL or file) rendered on the eased curve: (new source path, `clip` metadata)."""
    from routes.result_source import read_bytes, save_inline
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.mp4"), os.path.join(tmp, "out.mp4")
        with open(src, "wb") as f:
            f.write(read_bytes(source))
        play, ramp, settle = loop.get("play_s", 30), loop.get("ramp_s", 8), loop.get("settle_s", 3)
        motion = ease_file(src, dst, play, ramp, settle)
        with open(dst, "rb") as f:
            path = save_inline(f.read(), "eased.mp4", job_id)
    return path, {"eased": True, "play_s": play, "motion": round(motion, 2),
                  "rest_s": rest_range(motion, loop.get("rest_s", [30, 180]))}
