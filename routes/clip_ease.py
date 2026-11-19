"""A screen clip's slow, eased play, rendered into the file (owner, 19 Nov: "the ease-in and ease-out actually
matters ... if that's not smooth enough, produce longer mp4s with the ease-in/out rendered into the mp4 and play them
at full speed").

The browser can't do it: slowing a clip with playbackRate only holds each frame longer (at 0.065x a 24 fps frame
stays up for 0.6 s), so the start and end of the old loop stepped. Here every output frame is placed on an eased
curve: the clip starts from rest, rises over `ramp_s` to a steady pace and comes back to rest over another `ramp_s`.
A position that falls between two of the worker's (already 3x interpolated) frames is a blend of the two, which for
the slow, subtle motion these clips have is a true in-between. The last `settle_s` fade into frame 0, so the clip
ends on exactly the picture it starts on, and the TV can rest on it and play again with no jump.

A catalogue entry with `loop` ({"play_s", "ramp_s", "settle_s", "rest_s": [shortest, longest]}) gets this. The clip
carries its own `clip` metadata in the file (the MP4 comment tag, as JSON {"clip": {...}}: the app's convention for an
MP4's metadata, routes/utils._extract_mp4_comment_json), so any copy of it, published or republished from a bucket,
still says how to play it. The TV page reads it from the head of the file and plays the clip at 1x, resting between
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
    import cv2
    return cv2.resize(f, (f.shape[1] // 8, f.shape[0] // 8), interpolation=cv2.INTER_AREA).astype(np.float32)


class _OnTheStill:
    """The clip's motion laid onto the full-size still (owner, 19 Nov: the clip on the 4K TV looked blurry; "there
    should be a completely invisible join between still image and video"). The video model draws 1344x768, the
    screen's still is 3840x2160. Each frame is the still plus the clip's change since its first frame, upscaled;
    where the clip really moves (its change is large) the upscaled clip itself shows instead, so nothing sharp
    from the still ghosts over the motion. Where nothing moves, the frame is the still, pixel for pixel, and the
    first and last frames are exactly the still."""

    LOW, HIGH = 3.0, 12.0   # change (0-255, smoothed) below which the still shows, above which the clip does

    def __init__(self, still_path, first, w, h):
        import cv2
        self.cv2 = cv2
        s = cv2.cvtColor(cv2.imread(str(still_path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        H, W = s.shape[:2]
        if W > 3840:
            H, W = round(H * 3840 / W), 3840
            s = cv2.resize(s, (W, H), interpolation=cv2.INTER_AREA)
        self.size = (W // 2 * 2, H // 2 * 2)
        s = s[: self.size[1], : self.size[0]]
        self.still = s.astype(np.int16)
        self.first = first
        self.base = cv2.resize(first, self.size, interpolation=cv2.INTER_CUBIC) - s   # the clip's picture minus the still
        self.sx, self.sy = self.size[0] / w, self.size[1] / h
        self.up = np.empty_like(self.still)                 # reused every frame: no fresh 50 MB arrays
        self.out = np.empty_like(self.still)
        self.u8 = np.empty(self.still.shape, np.uint8)

    def __call__(self, low):
        """The full-size frame (uint8) for a clip frame `low` (float32, the clip's size). In int16, and the clip's own
        picture only where it moves: a whole 4K frame in float32 took ~0.24 s."""
        cv2 = self.cv2
        d = cv2.subtract(low, self.first)
        cv2.resize(np.rint(d).astype(np.int16), self.size, dst=self.up, interpolation=cv2.INTER_LINEAR)
        out = cv2.add(self.still, self.up, dst=self.out)
        a = cv2.GaussianBlur(cv2.cvtColor(np.abs(d), cv2.COLOR_RGB2GRAY), (0, 0), 3)
        m = np.clip((a - self.LOW) / (self.HIGH - self.LOW), 0, 1)
        ys, xs = np.nonzero(m)
        if len(ys):
            x0, x1 = max(0, int(xs.min() - 2)), min(m.shape[1], int(xs.max() + 3))
            y0, y1 = max(0, int(ys.min() - 2)), min(m.shape[0], int(ys.max() + 3))
            X0, X1, Y0, Y1 = int(x0 * self.sx), int(x1 * self.sx), int(y0 * self.sy), int(y1 * self.sy)
            mm = m[y0:y1, x0:x1]
            M = cv2.resize(mm * mm * (3 - 2 * mm), (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_LINEAR)
            roi = out[Y0:Y1, X0:X1]
            mix = cv2.multiply(cv2.merge([M, M, M]), self.base[Y0:Y1, X0:X1])
            cv2.add(roi, mix, dst=roi, dtype=cv2.CV_16S)
        cv2.max(out, 0, dst=out)
        return cv2.convertScaleAbs(out, dst=self.u8)


def fits(still_path, w, h):
    """The still has the clip's shape (it was made from it), so the clip can be laid onto it."""
    try:
        from PIL import Image
        W, H = Image.open(still_path).size
        return abs(W / H - w / h) < 0.03 * (w / h) and W > w   # the video model stretches 16:9 into 1344x768
    except Exception:
        return False


def ease_file(src, dst, play_s=30.0, ramp_s=8.0, settle_s=3.0, still=None):
    """Render src (any clip) into dst on the eased curve, at the size of `still` when it is given (the full-size
    picture the clip was made from: see _OnTheStill); returns how much of the picture moves (mean |frame - frame 0|)."""
    w, h, n = _probe(src)
    size = w * h * 3
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    frames, read, motion, ref = {}, -1, 0.0, None
    first = None
    lay = None

    def encoder(W, H):
        return subprocess.Popen(["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
                                 "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264",
                                 "-preset", "veryfast" if W > 2000 else "medium", "-crf", "17", "-pix_fmt", "yuv420p",
                                 "-movflags", "+faststart", dst], stdin=subprocess.PIPE)

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

    enc = None
    try:
        first = frame(0).astype(np.float32)
        if still and fits(still, w, h):
            lay = _OnTheStill(still, first, w, h)
        enc = encoder(*(lay.size if lay else (w, h)))
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
            enc.stdin.write(lay(out).tobytes() if lay else np.clip(out + 0.5, 0, 255).astype(np.uint8).tobytes())
        frame(n - 1)                     # read on to the end, so the motion counts the whole clip
    finally:
        dec.stdout.close()
        dec.wait()
        if enc:
            enc.stdin.close()
        if not enc or enc.wait() != 0:
            raise RuntimeError("ffmpeg could not write the eased clip")
    return motion


def _no_thp_stalls():
    """NumPy's huge-page advice off (see stream_service/gpu.py no_thp_stalls): on this host each 4K array otherwise
    stalls in memory compaction (a first 4K render here spent 4 of its 6 minutes in system time)."""
    try:
        from numpy._core.multiarray import _set_madvise_hugepage
    except ImportError:
        try:
            from numpy.core.multiarray import _set_madvise_hugepage
        except ImportError:
            return
    _set_madvise_hugepage(False)


def ease_result(source, loop, job_id, still=None):
    """A finished job's clip (URL or file) rendered on the eased curve, onto the full-size `still` it was made from
    when given: (new source path, `clip` metadata)."""
    from routes.result_source import read_bytes, save_inline
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.mp4"), os.path.join(tmp, "out.mp4")
        with open(src, "wb") as f:
            f.write(read_bytes(source))
        play, ramp, settle = loop.get("play_s", 30), loop.get("ramp_s", 8), loop.get("settle_s", 3)
        # in its own process: minutes of 4K work must not sit in Flask's (room_service lives there too)
        import sys
        run = subprocess.run(["nice", "-n", "10", sys.executable, "-m", "routes.clip_ease", src, dst, str(play),
                              str(ramp), str(settle), still or ""], capture_output=True, text=True,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if run.returncode != 0:
            raise RuntimeError(f"easing the clip failed: {run.stderr.strip()[-300:]}")
        motion = float(run.stdout.strip().splitlines()[-1])
        clip = {"eased": True, "pace": loop.get("pace", "slow"), "play_s": play, "motion": round(motion, 2),
                "rest_s": rest_range(motion, loop.get("rest_s", [30, 180]))}
        tagged = os.path.join(tmp, "tagged.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", dst, "-c", "copy", "-metadata",
                        "comment=" + json.dumps({"clip": clip}), "-movflags", "+faststart", tagged], check=True)
        with open(tagged, "rb") as f:
            path = save_inline(f.read(), "eased.mp4", job_id)
    return path, clip


if __name__ == "__main__":
    import sys
    _no_thp_stalls()
    a = sys.argv[1:]
    print(ease_file(a[0], a[1], float(a[2]), float(a[3]), float(a[4]), a[5] or None))
