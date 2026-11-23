"""A screen clip's eased play, rendered on the GPU: the batch job for routes/clip_ease.py (owner, 23 Nov: "if we _are_
doing it locally it should be local GPU enabled"; on the CPU a 4K frame took ~0.2 s to lay onto the still and as long
again to encode, 1-5 minutes a clip).

The app keeps the ease itself (the curve, the pace, the rests): it sends each output frame's place in the source,
``frames`` = [[x, wgt], ...] (x a fractional source frame, wgt how far that frame has faded into frame 0). Here each
frame is the blend of the two source frames around x, laid onto the full-size still the clip was made from (the same
picture as routes/clip_ease._OnTheStill: the still plus the clip's change since its first frame, upscaled, and where
the clip really moves the upscaled clip itself) or, without a still, the frame at its own size. It is converted to NV12
(BT.601 limited range, untagged, as ffmpeg's rgb24 -> yuv420p gave the CPU's clips) and encoded by the GPU's NVENC
without leaving it; ffmpeg only puts the H.264 stream in the MP4.

args: {src, still (or null), dst, fps, frames}. Writes dst (via a temp file beside it, so a job killed for a live TV
leaves no partial file) and dst + ".json" {"motion": mean |frame - frame 0| over the clip, on 8x8 blocks}.
"""
from __future__ import annotations

import json
import os
import subprocess

import numpy as np

POOL_BYTES = 1_536 * 2**20     # the job's CuPy pool (one 4K job: ~0.3 GB)
# NVENC H.264: 40 Mbit/s with a floor on quality (qmax 19) matches x264's crf 17 here (measured 23 Nov against a lossless
# render of the same frames: 46.0 dB luma, worst frame 46.3, x264 45.9 / 45.9; files ~1.4x the size)
ENC = {"preset": "P5", "tuning_info": "high_quality", "rc": "vbr", "bitrate": "40000000", "maxbitrate": "80000000",
       "vbvbufsize": "80000000", "qmax": "19", "aq": "8"}
LOW, HIGH = 3.0, 12.0          # as clip_ease._OnTheStill: change (0-255, smoothed) below which the still shows, above
                               # which the clip does

_SRC = r"""
__device__ __forceinline__ float tap(const float* p, int w, int h, int c, float fx, float fy, bool q) {
    int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    float ax = fx - x0, ay = fy - y0;
    if (x0 < 0) { x0 = 0; ax = 0.f; } if (y0 < 0) { y0 = 0; ay = 0.f; }
    if (x0 >= w - 1) { x0 = w - 1; ax = 0.f; } if (y0 >= h - 1) { y0 = h - 1; ay = 0.f; }
    int x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    float a = p[(y0 * w + x0) * c], b = p[(y0 * w + x1) * c], d = p[(y1 * w + x0) * c], e = p[(y1 * w + x1) * c];
    if (q) { a = rintf(a); b = rintf(b); d = rintf(d); e = rintf(e); }
    return (a + (b - a) * ax) * (1.f - ay) + (d + (e - d) * ax) * ay;
}
// One thread per 2x2 block: each pixel as _OnTheStill makes it (bilinear upscales, as OpenCV's INTER_LINEAR), then
// BT.601 limited-range Y and the block's averaged chroma.
extern "C" __global__ void compose_nv12(const float* frame, const float* diff, const float* mask,
                                        const unsigned char* still, const float* base, unsigned char* nv12,
                                        int W, int H, int w, int h, int on_still) {
    int bx = blockIdx.x * blockDim.x + threadIdx.x, by = blockIdx.y * blockDim.y + threadIdx.y;
    if (bx >= W / 2 || by >= H / 2) return;
    float sx = (float)w / W, sy = (float)h / H, cb = 0.f, cr = 0.f;
    for (int k = 0; k < 4; k++) {
        int X = bx * 2 + (k & 1), Y = by * 2 + (k >> 1);
        float rgb[3];
        if (on_still) {
            float fx = (X + .5f) * sx - .5f, fy = (Y + .5f) * sy - .5f;
            float m = tap(mask, w, h, 1, fx, fy, false);
            for (int c = 0; c < 3; c++) {
                size_t i = ((size_t)Y * W + X) * 3 + c;
                float up = rintf(tap(diff + c, w, h, 3, fx, fy, true));
                rgb[c] = fminf(fmaxf(still[i] + up + rintf(m * base[i]), 0.f), 255.f);
            }
        } else {
            for (int c = 0; c < 3; c++)
                rgb[c] = floorf(fminf(fmaxf(frame[((size_t)Y * W + X) * 3 + c] + .5f, 0.f), 255.f));
        }
        float y = 16.f + 0.256788f * rgb[0] + 0.504129f * rgb[1] + 0.097906f * rgb[2];
        nv12[(size_t)Y * W + X] = (unsigned char)fminf(fmaxf(y + .5f, 0.f), 255.f);
        cb += -0.148223f * rgb[0] - 0.290993f * rgb[1] + 0.439216f * rgb[2];
        cr += 0.439216f * rgb[0] - 0.367788f * rgb[1] - 0.071427f * rgb[2];
    }
    size_t uv = (size_t)W * H + (size_t)by * W + bx * 2;
    nv12[uv] = (unsigned char)fminf(fmaxf(128.f + cb / 4.f + .5f, 0.f), 255.f);
    nv12[uv + 1] = (unsigned char)fminf(fmaxf(128.f + cr / 4.f + .5f, 0.f), 255.f);
}
"""


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=width,height,nb_read_packets", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.strip().split(",")
    return int(out[0]), int(out[1]), int(out[2])


def _blocks(f):
    """8x8 block means (as clip_ease._blocks): the picture as the eye takes it in, without the grain."""
    h, w = f.shape[0] // 8 * 8, f.shape[1] // 8 * 8
    return f[:h, :w].reshape(h // 8, 8, w // 8, 8, 3).mean(axis=(1, 3), dtype=np.float32)


def _still(path, first):
    """The still at most 3840 wide, even-sized (uint8), and the clip's first frame upscaled to it (bicubic, float)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if im.width > 3840:
        im = im.resize((3840, round(im.height * 3840 / im.width)), Image.LANCZOS)
    W, H = im.width // 2 * 2, im.height // 2 * 2
    s = np.asarray(im)[:H, :W]
    up = np.asarray(Image.fromarray(first).resize((W, H), Image.BICUBIC), np.float32)
    return s, up - s


def run(args: dict, progress) -> None:
    import cupy as cp
    import PyNvVideoCodec as nvc
    from cupyx.scipy import ndimage
    from stream_service.encode.nvenc import Surface

    cp.get_default_memory_pool().set_limit(size=POOL_BYTES)
    src, dst, fps, frames = args["src"], args["dst"], float(args["fps"]), args["frames"]
    w, h, n = _probe(src)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    cache, read, motion, ref = {}, -1, 0.0, None

    def frame(i):  # positions never go back: keep the frames in use (and frame 0)
        nonlocal read, motion, ref
        i = min(i, n - 1)
        while read < i:
            buf = dec.stdout.read(w * h * 3)
            if len(buf) < w * h * 3:            # fewer frames than the container said: hold the last one
                return cache[read]
            read += 1
            f = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            cache[read] = f
            small = _blocks(f)
            if read:
                motion = max(motion, float(np.abs(small - ref).mean()))
            else:
                ref = small
            for k in [k for k in cache if 0 < k < read - 1]:
                del cache[k]
        return cache[i]

    tmp_h264, tmp = dst + ".part.h264", dst + ".part.mp4"
    try:
        first8 = frame(0)
        first = cp.asarray(first8, dtype=cp.float32)
        still = base = None
        if args.get("still"):
            s, b = _still(args["still"], first8)
            still, base = cp.asarray(s), cp.asarray(b)
        W, H = (still.shape[1], still.shape[0]) if still is not None else (w, h)
        kernel = cp.RawKernel(_SRC, "compose_nv12")
        nv12 = cp.empty((H * 3 // 2, W), cp.uint8)
        gop = str(max(1, round(fps)))   # a keyframe a second: the publisher's re-encode for quick stills has nothing to do
        enc = nvc.CreateEncoder(W, H, "NV12", False, cudacontext=int(cp.cuda.driver.ctxGetCurrent()),
                                cudastream=int(cp.cuda.get_current_stream().ptr), gpu_id="0", codec="h264",
                                fps=f"{fps:g}", gop=gop, idrperiod=gop, bf="0", repeatspspps="1", **ENC)
        surface = Surface(nv12, W, H)
        grid = ((W // 2 + 15) // 16, (H // 2 + 15) // 16)
        with open(tmp_h264, "wb") as out:
            def write(packets):
                for p in packets:
                    out.write(bytes(p["data"]) if isinstance(p, dict) else bytes(p))

            for k, (x, wgt) in enumerate(frames):
                i = int(x)
                frac = x - i
                f = cp.asarray(frame(i), dtype=cp.float32)
                if frac >= 1e-3:
                    f += (cp.asarray(frame(i + 1), dtype=cp.float32) - f) * cp.float32(frac)
                if wgt:
                    f += (first - f) * cp.float32(wgt)
                if still is not None:
                    d = f - first
                    ad = cp.abs(d)
                    gray = ad[..., 0] * cp.float32(0.299) + ad[..., 1] * cp.float32(0.587) + ad[..., 2] * cp.float32(0.114)
                    m = cp.clip((ndimage.gaussian_filter(gray, 3, mode="mirror", truncate=4.0) - LOW) / (HIGH - LOW), 0, 1)
                    mask = cp.ascontiguousarray(m * m * (3 - 2 * m), dtype=cp.float32)
                    kernel(grid, (16, 16), (f, d, mask, still, base, nv12, np.int32(W), np.int32(H), np.int32(w),
                                            np.int32(h), np.int32(1)))
                else:
                    kernel(grid, (16, 16), (f, f, f, nv12, f, nv12, np.int32(W), np.int32(H), np.int32(w),
                                            np.int32(h), np.int32(0)))
                write(enc.Encode(surface))
                if k % 10 == 0:
                    progress(k / len(frames))
            write(enc.EndEncode())
        frame(n - 1)                    # read on to the end, so the motion counts the whole clip
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", f"{fps:g}", "-i", tmp_h264, "-c", "copy",
                        "-movflags", "+faststart", tmp], check=True)
        os.replace(tmp, dst)
        with open(dst + ".json", "w") as f_:
            json.dump({"motion": motion}, f_)
        progress(1.0)
    finally:
        dec.stdout.close()
        dec.kill()
        dec.wait()
        for p in (tmp_h264, tmp):
            if os.path.exists(p):
                os.remove(p)
