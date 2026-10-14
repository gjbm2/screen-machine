"""Device helpers shared by the plug-in kernels (CUDA source prepended to each plug-in's kernel).

Frames are (H, W, 4) uint8 sRGB-encoded RGBA (alpha ignored, written 255), as everywhere in the renderer.
Blends are in sRGB-encoded space, like CSS (see gpu.py's crossfade).
"""

HELPERS = r"""
__device__ __forceinline__ float clamp01(float v) { return fminf(fmaxf(v, 0.f), 1.f); }
__device__ __forceinline__ float smooth(float e0, float e1, float x) {
    const float t = clamp01((x - e0) / (e1 - e0));
    return t * t * (3.f - 2.f * t);
}
__device__ __forceinline__ float4 ld(const uchar4* __restrict__ img, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1);
    const uchar4 p = img[y * W + x];
    return make_float4(p.x, p.y, p.z, 255.f);
}
// Bilinear sample at continuous pixel coords (pixel centres at i + 0.5), clamped at the edges.
__device__ float4 bilin(const uchar4* __restrict__ img, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const float4 a = ld(img, x0, y0, W, H), b = ld(img, x0 + 1, y0, W, H);
    const float4 c = ld(img, x0, y0 + 1, W, H), d = ld(img, x0 + 1, y0 + 1, W, H);
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 255.f);
}
__device__ __forceinline__ float4 mix4(float4 a, float4 b, float t) {
    return make_float4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, 255.f);
}
__device__ __forceinline__ uchar4 pack(float4 c) {
    return make_uchar4((unsigned char)fminf(fmaxf(c.x + 0.5f, 0.f), 255.f),
                       (unsigned char)fminf(fmaxf(c.y + 0.5f, 0.f), 255.f),
                       (unsigned char)fminf(fmaxf(c.z + 0.5f, 0.f), 255.f), 255);
}
__device__ __forceinline__ float luma(float4 c) { return (0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z) / 255.f; }
// Integer hash -> [0, 1).
__device__ __forceinline__ unsigned int hash_u(unsigned int x) {
    x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16; return x;
}
__device__ __forceinline__ float hash1(unsigned int a, unsigned int b = 0u, unsigned int c = 0u) {
    return (hash_u(a * 0x9E3779B1U ^ hash_u(b * 0x85EBCA77U ^ hash_u(c + 0x27d4eb2dU))) >> 8) * (1.f / 16777216.f);
}
// Value noise on an integer lattice with a smooth interpolant, and 4-octave fbm in ~[0, 1].
__device__ float vnoise(float x, float y, unsigned int seed) {
    const float fx = floorf(x), fy = floorf(y);
    const int ix = (int)fx, iy = (int)fy;
    float tx = x - fx, ty = y - fy;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty);
    const float a = hash1(ix, iy, seed), b = hash1(ix + 1, iy, seed);
    const float c = hash1(ix, iy + 1, seed), d = hash1(ix + 1, iy + 1, seed);
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
__device__ float fbm(float x, float y, unsigned int seed) {
    float s = 0.f, amp = 0.5f, norm = 0.f;
    #pragma unroll
    for (int o = 0; o < 4; ++o) { s += amp * vnoise(x, y, seed + 17u * o); norm += amp; x *= 2.03f; y *= 2.03f; amp *= 0.5f; }
    return s / norm;
}
"""


def launch_2d(kernel, w: int, h: int, args: tuple) -> None:
    kernel(((w + 31) // 32, (h + 7) // 8, 1), (32, 8, 1), args)
