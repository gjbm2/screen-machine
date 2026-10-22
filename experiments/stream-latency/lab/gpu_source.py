"""GPU-rendered frame source (CUDA via CuPy) — the "complex local GPU display" stand-in.

One CUDA kernel renders an animated, domain-warped Julia fractal with smooth colouring plus fine
high-frequency detail (deliberately hard to encode), converts to BT.709 limited-range NV12 on the
GPU (one thread per 2x2 block, so chroma is averaged correctly), and the frame is copied into pinned
host memory for the encoder pipe. The shared Stamper then burns in barcode / clock / sweep bar.

Works in WSL2 (CUDA is supported there; OpenGL is not CUDA-interoperable in WSL, which is why the
renderer is CUDA rather than GL). Needs `cupy-cuda12x` + `nvidia-cuda-nvrtc-cu12` (see README).

complexity: 0..3 -> fractal iterations 32/96/192/384 and detail amplitude.
"""
from __future__ import annotations

import numpy as np

from .source import Stamper

_KERNEL = r"""
extern "C" __global__
void render_nv12(unsigned char* Y, unsigned char* UV, const int W, const int H,
                 const float t, const int iters, const float detail)
{
    const int bx = blockIdx.x * blockDim.x + threadIdx.x;   // 2x2 block coordinates
    const int by = blockIdx.y * blockDim.y + threadIdx.y;
    if (bx >= W / 2 || by >= H / 2) return;
    const float cr = 0.7885f * cosf(0.21f * t), ci = 0.7885f * sinf(0.21f * t);
    const float zoom = 1.25f + 0.25f * sinf(0.13f * t);
    float rs = 0.f, gs = 0.f, bs = 0.f;
    for (int dy = 0; dy < 2; ++dy) {
        for (int dx = 0; dx < 2; ++dx) {
            const int x = 2 * bx + dx, y = 2 * by + dy;
            const float u = (x - 0.5f * W) / (0.5f * H) * zoom;
            const float v = (y - 0.5f * H) / (0.5f * H) * zoom;
            // domain warp: makes the whole field move organically, every frame changes everywhere
            const float wx = u + 0.12f * sinf(3.1f * v + 0.9f * t) + 0.05f * detail * sinf(9.3f * u - 1.7f * t);
            const float wy = v + 0.12f * cosf(2.7f * u - 0.7f * t) + 0.05f * detail * cosf(8.1f * v + 1.3f * t);
            float zr = wx, zi = wy, m2 = 0.f;
            int i = 0;
            for (; i < iters; ++i) {
                const float nzr = zr * zr - zi * zi + cr;
                zi = 2.f * zr * zi + ci;
                zr = nzr;
                m2 = zr * zr + zi * zi;
                if (m2 > 256.f) break;
            }
            float r, g, b;
            if (i >= iters) {
                r = g = b = 0.03f;
            } else {
                const float s = (i + 1.f - log2f(0.5f * log2f(m2))) / 48.f;  // smooth iteration count
                const float ph = 6.2831853f * (2.2f * s + 0.04f * t);
                r = 0.5f + 0.5f * cosf(ph);
                g = 0.5f + 0.5f * cosf(ph + 2.0944f);
                b = 0.5f + 0.5f * cosf(ph + 4.1888f);
            }
            // fine moving texture: stresses the encoder like real "complex" content would
            const float n = 0.07f * detail * sinf(0.91f * x + 19.f * t) * sinf(1.13f * y - 17.f * t);
            r = fminf(fmaxf(r + n, 0.f), 1.f);
            g = fminf(fmaxf(g + n, 0.f), 1.f);
            b = fminf(fmaxf(b + n, 0.f), 1.f);
            Y[y * W + x] = (unsigned char)(16.f + 219.f * (0.2126f * r + 0.7152f * g + 0.0722f * b) + 0.5f);
            rs += r; gs += g; bs += b;
        }
    }
    rs *= 0.25f; gs *= 0.25f; bs *= 0.25f;
    UV[by * W + 2 * bx]     = (unsigned char)(128.f + 224.f * (-0.1146f * rs - 0.3854f * gs + 0.5f * bs) + 0.5f);
    UV[by * W + 2 * bx + 1] = (unsigned char)(128.f + 224.f * (0.5f * rs - 0.4542f * gs - 0.0458f * bs) + 0.5f);
}
"""


class CudaSource:
    def __init__(self, width: int, height: int, fps: int, complexity: int = 2, device: int = 0):
        import cupy as cp  # imported lazily: only this source needs CuPy

        if width % 2 or height % 2:
            raise ValueError("NV12 needs even dimensions")
        self.cp = cp
        self.w, self.h, self.fps = width, height, fps
        self.device = cp.cuda.Device(device)
        self.device.use()
        self.kernel = cp.RawKernel(_KERNEL, "render_nv12")
        n = width * height * 3 // 2
        self.d_frame = cp.empty(n, cp.uint8)
        self.d_y = self.d_frame[: width * height]
        self.d_uv = self.d_frame[width * height:]
        self._pinned = cp.cuda.alloc_pinned_memory(n)
        self.frame = np.frombuffer(self._pinned, np.uint8, n)
        self.Y = self.frame[: width * height].reshape(height, width)
        self.UV = self.frame[width * height:].reshape(height // 2, width)
        self.stream = cp.cuda.Stream(non_blocking=True)
        self.iters = (32, 96, 192, 384)[complexity]
        self.detail = (0.0, 0.5, 1.0, 2.0)[complexity]
        self.block = (16, 16, 1)
        self.grid = ((width // 2 + 15) // 16, (height // 2 + 15) // 16, 1)
        self.stamper = Stamper(width, height, fps)
        self.geom = self.stamper.geom
        self.gpu_ms = 0.0  # kernel + device->host copy of the last frame (CUDA events)
        self._ev0, self._ev1 = cp.cuda.Event(), cp.cuda.Event()

    def render(self, frame_id: int, capture_ms: float) -> memoryview:
        cp = self.cp
        with self.device, self.stream:
            self._ev0.record(self.stream)
            self.kernel(self.grid, self.block,
                        (self.d_y, self.d_uv, np.int32(self.w), np.int32(self.h),
                         np.float32(frame_id / self.fps), np.int32(self.iters), np.float32(self.detail)))
            self.d_frame.get(stream=self.stream, out=self.frame)
            self._ev1.record(self.stream)
        self._ev1.synchronize()
        self.gpu_ms = cp.cuda.get_elapsed_time(self._ev0, self._ev1)
        self.stamper.stamp(self.Y, self.UV, frame_id, capture_ms)
        return memoryview(self.frame)
