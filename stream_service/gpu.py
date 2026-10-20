"""GPU helpers shared by the renderer and the plug-ins (CuPy).

* :class:`RenderContext` - what a plug-in's ``setup(ctx, ...)`` gets: frame size, fps, the CUDA device,
  the stream to launch on, a pooled frame-buffer allocator and the core kernels.
* Core kernels: RGBA -> NV12 (BT.709 limited range; one thread per 2x2 block, chroma averaged over the
  block as in the lab's gpu_source.py), crossfade (sRGB-space blend, like CSS opacity), solid fill and
  RGB -> RGBA expansion for uploaded stills.

Frames are CuPy ``uint8`` arrays of shape (H, W, 4) with sRGB-encoded values; alpha is ignored and
written as 255.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

import numpy as np

_CORE_SRC = r"""
// BT.709, limited range.  Inputs are sRGB-encoded 8-bit values used directly as R'G'B' (no linearisation):
// the stream is tagged so that the decoder's YCbCr -> R'G'B' gives back the same code values an <img> shows.
__device__ __forceinline__ float luma709(float r, float g, float b) {
    return 0.2126f * r + 0.7152f * g + 0.0722f * b;
}
__device__ __forceinline__ unsigned char u8(float v) {
    return (unsigned char)fminf(fmaxf(v + 0.5f, 0.f), 255.f);
}

extern "C" __global__
void rgba_to_nv12(const uchar4* __restrict__ rgba, unsigned char* __restrict__ Y,
                  unsigned char* __restrict__ UV, const int W, const int H)
{
    const int bx = blockIdx.x * blockDim.x + threadIdx.x;   // 2x2 block coordinates
    const int by = blockIdx.y * blockDim.y + threadIdx.y;
    if (bx >= (W >> 1) || by >= (H >> 1)) return;
    const int x = bx << 1, y = by << 1;
    const uchar4 p00 = rgba[y * W + x],       p01 = rgba[y * W + x + 1];
    const uchar4 p10 = rgba[(y + 1) * W + x], p11 = rgba[(y + 1) * W + x + 1];
    const float k = 219.f / 255.f;
    *reinterpret_cast<uchar2*>(Y + y * W + x) =
        make_uchar2(u8(16.f + k * luma709(p00.x, p00.y, p00.z)), u8(16.f + k * luma709(p01.x, p01.y, p01.z)));
    *reinterpret_cast<uchar2*>(Y + (y + 1) * W + x) =
        make_uchar2(u8(16.f + k * luma709(p10.x, p10.y, p10.z)), u8(16.f + k * luma709(p11.x, p11.y, p11.z)));
    const float r = 0.25f * ((float)p00.x + p01.x + p10.x + p11.x);
    const float g = 0.25f * ((float)p00.y + p01.y + p10.y + p11.y);
    const float b = 0.25f * ((float)p00.z + p01.z + p10.z + p11.z);
    const float c = 224.f / 255.f;
    const float cb = 128.f + c * (-0.114572f * r - 0.385428f * g + 0.5f * b);
    const float cr = 128.f + c * (0.5f * r - 0.454153f * g - 0.045847f * b);
    *reinterpret_cast<uchar2*>(UV + by * W + x) = make_uchar2(u8(cb), u8(cr));
}

// The live diagnostics panel (hud.py; docs/LIVE_DIAGNOSTICS.md): blended into the NV12 frame over a fixed, clamped
// rectangle, one thread per 2x2 block.  Luma: near-black panel, greyscale text from the glyph atlas; chroma pulled to
// neutral (amber on warning lines' text).  Row 0's last 7 cells show the frame number and its last cell the beat lamp
// (both scalars, so they change every frame without an upload).  No data-dependent loops; codes are clamped.
extern "C" __global__
void hud_nv12(unsigned char* __restrict__ Y, unsigned char* __restrict__ UV, const int W, const int H,
              const int x0, const int y0, const int pw, const int ph,
              const unsigned char* __restrict__ atlas, const int cw, const int ch, const int nglyph,
              const unsigned char* __restrict__ grid, const unsigned char* __restrict__ tone, const int cols,
              const int rows, const int pad, const float a, const float lamp, const int frame_no)
{
    const int bx = blockIdx.x * blockDim.x + threadIdx.x, by = blockIdx.y * blockDim.y + threadIdx.y;
    if (bx >= (pw >> 1) || by >= (ph >> 1)) return;
    const int px = x0 + (bx << 1), py = y0 + (by << 1);
    if (px + 1 >= W || py + 1 >= H || px < 0 || py < 0) return;
    float cov_sum = 0.f;
    int warn = 0;
    #pragma unroll
    for (int dy = 0; dy < 2; ++dy) {
        #pragma unroll
        for (int dx = 0; dx < 2; ++dx) {
            const int x = px + dx, y = py + dy;
            const int lx = x - x0 - pad, ly = y - y0 - pad;
            float cov = 0.f;
            if (lx >= 0 && ly >= 0) {
                const int cx = lx / cw, cy = ly / ch;
                if (cx < cols && cy < rows) {
                    int g = grid[cy * cols + cx];
                    if (cy == 0 && cx >= cols - 8 && cx < cols - 1) {      // the frame number, right-aligned
                        int v = frame_no;
                        const int d = cols - 2 - cx;
                        #pragma unroll
                        for (int i = 0; i < 6; ++i) { if (i < d) v /= 10; }
                        g = 16 + (v % 10);                                   // '0' - 32
                    }
                    if (g < 0 || g >= nglyph) g = 0;
                    cov = atlas[(g * ch + (ly - cy * ch)) * cw + (lx - cx * cw)] * (1.f / 255.f);
                    if (cy == 0 && cx == cols - 1) cov = fminf(fmaxf(lamp, 0.f), 1.f);   // the beat lamp
                    if (tone[cy]) warn = 1;
                }
            }
            const float yv = 22.f + (232.f - 22.f) * cov;
            unsigned char* q = Y + y * W + x;
            *q = u8((float)*q + (yv - (float)*q) * a);
            cov_sum += cov;
        }
    }
    const float k = cov_sum * 0.25f;
    const float cb = warn ? 128.f - 70.f * k : 128.f, cr = warn ? 128.f + 45.f * k : 128.f;
    unsigned char* c = UV + (py >> 1) * W + px;
    c[0] = u8((float)c[0] + (cb - (float)c[0]) * a);
    c[1] = u8((float)c[1] + (cr - (float)c[1]) * a);
}

// out = a + (b - a) * p, per channel, in sRGB-encoded space (what CSS opacity does).  Two pixels per thread.
__device__ __forceinline__ unsigned int mix_px(const unsigned int a, const unsigned int b,
                                               const float p, const float q) {
    unsigned int o = 0xFF000000u;
    #pragma unroll
    for (int s = 0; s < 24; s += 8) {
        const float v = q * (float)((a >> s) & 255u) + p * (float)((b >> s) & 255u) + 0.5f;
        o |= ((unsigned int)v & 255u) << s;
    }
    return o;
}

extern "C" __global__
void crossfade(const uint2* __restrict__ a, const uint2* __restrict__ b, uint2* __restrict__ out,
               const int n2, const float p)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n2) return;
    const uint2 va = a[i], vb = b[i];
    const float q = 1.f - p;
    out[i] = make_uint2(mix_px(va.x, vb.x, p, q), mix_px(va.y, vb.y, p, q));
}

extern "C" __global__
void fill_u32(unsigned int* __restrict__ out, const int n, const unsigned int v)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = v;
}

extern "C" __global__
void rgb_to_rgba(const unsigned char* __restrict__ rgb, uchar4* __restrict__ out, const int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = make_uchar4(rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2], 255);
}
"""

_modules: dict[str, object] = {}
_modules_lock = threading.Lock()


def raw_module(source: str, options: tuple[str, ...] = ("--std=c++14",)):
    """A CuPy RawModule, compiled once per process (CuPy loads it per device on first use)."""
    import cupy as cp

    key = source + "\0" + " ".join(options)
    with _modules_lock:
        mod = _modules.get(key)
        if mod is None:
            mod = cp.RawModule(code=source, options=options)
            _modules[key] = mod
        return mod


class CoreKernels:
    def __init__(self) -> None:
        mod = raw_module(_CORE_SRC)
        self.k_nv12 = mod.get_function("rgba_to_nv12")
        self.k_crossfade = mod.get_function("crossfade")
        self.k_fill = mod.get_function("fill_u32")
        self.k_rgb_to_rgba = mod.get_function("rgb_to_rgba")
        self.k_hud = mod.get_function("hud_nv12")

    def rgba_to_nv12(self, rgba, nv12, w: int, h: int) -> None:
        y = nv12[: w * h]
        uv = nv12[w * h:]
        block = (32, 8, 1)
        grid = ((w // 2 + 31) // 32, (h // 2 + 7) // 8, 1)
        self.k_nv12(grid, block, (rgba, y, uv, np.int32(w), np.int32(h)))

    def crossfade(self, a, b, out, p: float) -> None:
        n2 = a.size // 8  # two RGBA pixels (8 bytes) per thread
        self.k_crossfade(((n2 + 255) // 256,), (256,), (a, b, out, np.int32(n2), np.float32(p)))

    def fill(self, out, rgb: tuple[int, int, int]) -> None:
        n = out.size // 4
        v = (255 << 24) | (int(rgb[2]) << 16) | (int(rgb[1]) << 8) | int(rgb[0])
        self.k_fill(((n + 255) // 256,), (256,), (out, np.int32(n), np.uint32(v)))

    def rgb_to_rgba(self, rgb, out) -> None:
        n = out.size // 4
        self.k_rgb_to_rgba(((n + 255) // 256,), (256,), (rgb, out, np.int32(n)))


class BufferPool:
    """Reusable (H, W, 4) uint8 frame buffers, so transitions do not allocate per frame."""

    def __init__(self, w: int, h: int, prealloc: int = 0):
        import cupy as cp

        self.cp = cp
        self.shape = (h, w, 4)
        self.free: list = [cp.empty(self.shape, cp.uint8) for _ in range(prealloc)]
        self.allocated = prealloc

    def acquire(self):
        if self.free:
            return self.free.pop()
        self.allocated += 1
        return self.cp.empty(self.shape, self.cp.uint8)

    def release(self, buf) -> None:
        self.free.append(buf)

    def reserve(self, n: int) -> None:
        """Allocate up front so at least ``n`` frames exist (worker thread, before the first effect is
        submitted): the render thread then never allocates for effect chains (R20)."""
        while self.allocated < n:
            self.allocated += 1
            self.free.append(self.cp.empty(self.shape, self.cp.uint8))


class GpuOps:
    """The layer tree's frame primitives on the GPU (layers.FrameOps): copies and the core kernels."""

    def __init__(self, ctx: "RenderContext"):
        self.ctx = ctx

    def copy(self, src, out) -> None:
        self.ctx.cp.copyto(out, src)

    def fill(self, out, rgb) -> None:
        self.ctx.kernels.fill(out, rgb)

    def crossfade(self, a, b, out, p: float) -> None:
        self.ctx.kernels.crossfade(a, b, out, float(p))


RENDER_PRIORITY = -5     # W1.9: the render stream first (CUDA clamps it to the device's greatest, most negative)
LOW_PRIORITY = 0         # prepare, prime and upload streams (the lowest)


def no_thp_stalls() -> None:
    """Large NumPy arrays without MADV_HUGEPAGE (a process's start; the service's and each render worker's).  NumPy
    advises huge pages for every array of 4 MB or more, and with THP defrag "madvise" (this WSL host) each fault in
    one compacts memory synchronously: on a fragmented host one 4K anchor read took 1.3-3.3 s of system time (16
    failed compactions, 20 Oct).  Without the advice the kernel still uses a huge page when one is free."""
    try:
        from numpy._core.multiarray import _set_madvise_hugepage
    except ImportError:
        try:
            from numpy.core.multiarray import _set_madvise_hugepage
        except ImportError:
            return
    _set_madvise_hugepage(False)


def priority_stream(cp: Any, priority: int) -> Any:
    """A non-blocking stream at ``priority``; a plain one if priorities are unavailable (degrade, never refuse)."""
    try:
        return cp.cuda.Stream(non_blocking=True, priority=priority)
    except Exception:
        return cp.cuda.Stream(non_blocking=True)


def stream_priority(cp: Any, stream: Any) -> Optional[int]:
    try:
        return int(cp.cuda.runtime.streamGetPriority(stream.ptr))
    except Exception:
        return None


class RenderContext:
    """Per-session GPU context.  ``stream`` is the session's render stream (highest priority, so a short render
    kernel never queues behind a prepare's full grid); plug-ins launch on the current stream (the render thread
    makes it current), so they need not pass it explicitly."""

    def __init__(self, gpu: int, width: int, height: int, fps: int, *, prealloc_buffers: int = 2):
        import cupy as cp

        self.cp = cp
        self.gpu = gpu
        self.width, self.height, self.fps = width, height, fps
        self.device = cp.cuda.Device(gpu)
        with self.device:
            self.stream = priority_stream(cp, RENDER_PRIORITY)
            self.stream_priority = stream_priority(cp, self.stream)   # as the device clamped it
            self.kernels = CoreKernels()
            self.pool = BufferPool(width, height, prealloc_buffers)

    def new_frame(self):
        return self.cp.empty((self.height, self.width, 4), self.cp.uint8)


def device_names() -> list[dict]:
    """[{index, name, cc}] for every CUDA device (no context is created)."""
    try:
        import cupy as cp

        out = []
        for i in range(cp.cuda.runtime.getDeviceCount()):
            p = cp.cuda.runtime.getDeviceProperties(i)
            out.append({"index": i, "name": p["name"].decode(errors="replace"),
                        "cc": (int(p["major"]), int(p["minor"])), "pci_bus": int(p["pciBusID"])})
        return out
    except Exception:
        return []


def supports_av1_encode(gpu: int) -> Optional[bool]:
    """NVENC AV1 needs Ada (compute capability 8.9) or newer.  None if unknown."""
    for d in device_names():
        if d["index"] == gpu:
            return d["cc"] >= (8, 9)
    return None
