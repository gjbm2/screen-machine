"""Voxel tunnel: fly down a procedurally carved tunnel of bevelled cubes, with emissive voxels that flare as a beat
wave rushes down the tube.

Source: https://www.shadertoy.com/view/ssXSzn ("Ray-Cast Octree"); https://www.shadertoy.com/view/sscGz4 ("Exodus")
Author: Jakob Thomsen (jt, 'Ray-Cast Octree', ssXSzn, CC0); Angelo / Aberration Creations (unjello, 'Exodus', CC0); lsdlive's look reference is CC-BY-NC-SA, not used
License: CC0-1.0
license_class: permissive
Ported from GLSL by smfx (ideas/changes: jt's emulated-recursion octree (subdivided cube with sub-cubes omitted)
        becomes a flat 2x2x2 / 4x4x4 sub-grid DDA inside full wall voxels near the camera, whose sub-cubes shrink
        away by hash instead of being dropped; the voxel tunnel / fog / glow look follows Exodus' voxel scenes.
        Written for this renderer: Amanatides-Woo DDA through a carved voxel volume (round .. square .. cave
        cross-section, sine ribs, value-noise carving, per-cell jitter), surface voxels as partially-filled
        (shrunken) cubes so cells grow and shrink instead of popping, face-uv bevel normals, Minecraft-style
        neighbour AO, emissive voxels with in-plane light bleed, a pulse wave read from a pulse history by depth,
        host-integrated path / palette / noise phases.  Vector helpers, camera, noise, tonemap and sRGB store come
        from stream_service/plugins/_lib/sdf3d.py (MIT; notices there).)

The tunnel is a voxel grid of cubes of ``voxel_size`` world units.  Each cell is filled by its distance from the
tunnel axis (a curving path) against the radius: ``tunnel_shape`` morphs the cross-section from round (0) to
square (1) and on to a noise-carved cave (2); ``ribs`` cuts sine rings; a little value noise and per-cell jitter
always roughen the surface.  Cells in a band around the wall are drawn as shrunken cubes (fill 0..1), so the
surface is a layer of smaller, floating blocks and the walls grow and shrink smoothly.  Near the camera
(``octree_depth`` > 0) full wall voxels are subdivided into 2^d sub-cubes and a hashed share of them shrinks away,
the octree look.  Rays walk the grid with a DDA (<= 120 cells, sub-grid <= 12) and stop on the first cube.

Macros: energy -> flight speed, emissive-voxel density, carve-noise animation, camera roll rate, sway and FOV,
emissive glow and headlight.  pulse -> an emissive wave travelling down the tunnel (amplitude read from a pulse
history by depth, so each beat is a ring of light rushing away), surface voxels swelling, a flight-rate kick and a
small (<= 12 %) gain lift.  speed scales every rate; drift rotates the voxel palette; voxel_size is regional (eased,
the grid rescales around the camera without popping); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAXS = 120            # coarse DDA steps
HN = 64               # pulse-history samples
HDT = 1.0 / 40.0      # pulse-history spacing (s)
WAVE_C = 24.0         # pulse-wave speed (world units / s, relative to the camera)
IZ_WRAP = 1 << 20     # global cell-z wrap (hash identity only; hours of flight)
NP = 160

# P layout (floats)
#  0..12 camera (cell units) | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density (per world unit)
#  29 voxel size | 30 radius | 31 squareness | 32 noise amp | 33 carve anim | 34 emissive ratio | 35 emissive gain
#  36 pulse | 37 bevel width (face fraction) | 38 octree depth | 39 near range | 40..45 path (Ax, ka, pha, Ay, kb, phb)
#  46 IZ0 (global cell-z offset) | 47 wave speed | 48 headlight | 49 fill band | 50 noise freq | 51 noise z phase
#  52 jitter | 53 inner cut | 54 outer cut | 55 palette z phase | 56 rib depth | 57 rib k | 58 rib phase | 59 pop
#  60 twinkle phase | 61 hist dt | 62 hist age of newest | 63 tmax (cells) | 64 seed | 65 octree removal max
#  66 core fade lo | 67 core fade hi | 68 cave | 69 bleed | 70 headlight range | 71..73 key dir | 80..143 history
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MAXS %(maxs)d
#define HN %(hn)d

__device__ __forceinline__ float cmp3(float3 v, int i) { return i == 0 ? v.x : (i == 1 ? v.y : v.z); }
__device__ __forceinline__ float3 axis3(int i, float s) {
    return f3(i == 0 ? s : 0.f, i == 1 ? s : 0.f, i == 2 ? s : 0.f);
}
// signed bevel weight across a face coordinate u in [0, 1]: +-1 at the edges, 0 inside, sign = which edge
__device__ __forceinline__ float sedge(float u, float bw) {
    const float e = fminf(u, 1.f - u);
    return (1.f - smooth(0.f, bw, e)) * (u > 0.5f ? 1.f : -1.f);
}
__device__ __forceinline__ float pickmax(float a, float b) { return fabsf(b) > fabsf(a) ? b : a; }

// Fill of voxel (ix, iy, iz) (local grid, z relative to IZ0) in 0..1; dzw = its world depth from the camera.
__device__ float cell_fill(int ix, int iy, int iz, const float* __restrict__ P, float& dzw) {
    const float vs = P[29];
    dzw = ((float)iz + 0.5f - P[2]) * vs;
    const float qx = ((float)ix + 0.5f) * vs - P[40] * sinf(P[41] * dzw + P[42]);
    const float qy = ((float)iy + 0.5f) * vs - P[43] * sinf(P[44] * dzw + P[45]);
    const float r = mix(sqrtf(qx * qx + qy * qy), fmaxf(fabsf(qx), fabsf(qy)), P[31]);
    if (r < P[53]) return 0.f;
    if (r > P[54]) return 1.f;
    const unsigned int sd = (unsigned int)P[64];
    const float an = P[33], nf = P[50];
    const float3 np = f3(qx * nf + 0.61f * an, qy * nf - 0.37f * an, P[51] + dzw * nf + 0.23f * an);
    float n = noise3(np, sd);
    if (P[68] > 0.01f) n = (n + 0.45f * noise3(np * 2.13f + f3(5.1f, 1.7f, 3.3f), sd + 3u)) * (1.f / 1.45f);
    n = n * 2.f - 1.f;
    const int izg = iz + (int)P[46];
    const float j = (hash1((unsigned int)ix, (unsigned int)iy, (unsigned int)izg ^ (sd * 0x2545F491u)) - 0.5f) * 2.f * P[52];
    const float rib = P[56] * (0.5f + 0.5f * sinf(P[57] * dzw + P[58]));
    const float d = r - (P[30] - rib) - n * P[32] + j + P[59];
    return smooth(-P[49], P[49], d) * smooth(P[66], P[67], r);
}
__device__ __forceinline__ float cell_emis(int ix, int iy, int izg, const float* __restrict__ P, float& h) {
    h = hash1((unsigned int)ix * 0x9E3779B1u + 0x632BE5ABu, (unsigned int)iy + (unsigned int)P[64] * 0x85EBCA77u,
              (unsigned int)izg);
    return clamp01((P[34] - h) * 40.f);
}
// Pulse-wave amplitude at world depth dzw: the pulse as it was dzw / c seconds ago.
__device__ float wave_at(float dzw, const float* __restrict__ P) {
    const float age = fmaxf(dzw, 0.f) / P[47];
    const float h0 = P[62];
    if (age < h0) return mix(P[36], P[80], age / fmaxf(h0, 1e-4f));
    const float kf = (age - h0) / P[61];
    if (kf >= (float)(HN - 1)) return 0.f;
    const int k = (int)kf;
    return mix(P[80 + k], P[81 + k], kf - (float)k) * (1.f - kf * (1.f / (float)(HN - 1)));
}

SDF_KERNEL
void voxel_tunnel(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float vs = P[29];
    const float3 inv = sdf_safe_rcp(rd);
    const float3 tdl = abs(inv);
    const int stx = rd.x > 0.f ? 1 : -1, sty = rd.y > 0.f ? 1 : -1, stz = rd.z > 0.f ? 1 : -1;
    int cx = (int)floorf(ro.x), cy = (int)floorf(ro.y), cz = (int)floorf(ro.z);
    float tmx = ((float)(cx + (stx > 0)) - ro.x) * inv.x;
    float tmy = ((float)(cy + (sty > 0)) - ro.y) * inv.y;
    float tmz = ((float)(cz + (stz > 0)) - ro.z) * inv.z;
    float tent = 0.f;
    const float od = P[38];
    const int N = od > 1.001f ? 4 : 2;
    const float fN = (float)N;
    const float pal_t = P[55] + P[26];

    bool hit = false;
    float th = 0.f, bh = 0.5f, bs = 1.f, hdzw = 0.f, hnf = 0.f;
    float3 bc = f3(0.f);
    int lev = 0, hx = 0, hy = 0, hz = 0;
    for (int i = 0; i < MAXS; ++i) {
        float dzw;
        const float f = cell_fill(cx, cy, cz, P, dzw);
        if (f > 0.003f) {
            const float3 cc = f3((float)cx + 0.5f, (float)cy + 0.5f, (float)cz + 0.5f);
            if (f < 0.999f) {                                    // surface voxel: a shrunken cube
                const float hh = 0.5f * f;
                const float2 tt = iBox(ro - cc, rd, f3(hh));
                if (tt.x <= tt.y && tt.y > 0.f) {
                    hit = true; th = fmaxf(tt.x, 0.f); bc = cc; bh = hh; bs = f; lev = 0;
                    hx = cx; hy = cy; hz = cz; hdzw = dzw; break;
                }
            } else {
                const float nfd = od > 0.001f ? 1.f - smooth(P[39] * 0.55f, P[39], dzw) : 0.f;
                if (nfd <= 0.001f) {                             // full voxel
                    hit = true; th = tent; bc = cc; bh = 0.5f; bs = 1.f; lev = 0;
                    hx = cx; hy = cy; hz = cz; hdzw = dzw; break;
                }
                // octree: walk the 2^d sub-grid of this voxel; hashed sub-cubes shrink away near the camera
                const float T1 = P[65] * fminf(od, 1.f) * nfd, T2 = P[65] * clamp01(od - 1.f) * nfd;
                const float3 base = f3((float)cx, (float)cy, (float)cz);
                float3 pe = (ro + rd * tent - base) * fN;
                pe = f3(fminf(fmaxf(pe.x, 1e-3f), fN - 1e-3f), fminf(fmaxf(pe.y, 1e-3f), fN - 1e-3f),
                        fminf(fmaxf(pe.z, 1e-3f), fN - 1e-3f));
                int sx = min(max((int)floorf(pe.x), 0), N - 1);
                int sy = min(max((int)floorf(pe.y), 0), N - 1);
                int sz = min(max((int)floorf(pe.z), 0), N - 1);
                float smx = ((float)(sx + (stx > 0)) - pe.x) * inv.x;
                float smy = ((float)(sy + (sty > 0)) - pe.y) * inv.y;
                float smz = ((float)(sz + (stz > 0)) - pe.z) * inv.z;
                float ts = 0.f;
                const unsigned int izg = (unsigned int)(cz + (int)P[46]);
                const unsigned int sd = (unsigned int)P[64];
                for (int j = 0; j < 12; ++j) {
                    const int px = N == 4 ? (sx >> 1) : sx, py = N == 4 ? (sy >> 1) : sy, pz = N == 4 ? (sz >> 1) : sz;
                    const unsigned int pid = (unsigned int)(px + 2 * py + 4 * pz);
                    float s = 1.f - clamp01((T1 - hash1((unsigned int)cx * 0x27D4EB2Fu + pid * 0x165667B1u,
                                                        (unsigned int)cy + sd * 0x9E3779B1u, izg)) * 8.f);
                    if (N == 4) {
                        const unsigned int sid = (unsigned int)(sx + 4 * sy + 16 * sz);
                        s *= 1.f - clamp01((T2 - hash1((unsigned int)cx * 0x61C88647u + sid * 0x2C1B3C6Du,
                                                       (unsigned int)cy + sd * 0x85EBCA77u, izg + 0x51u)) * 8.f);
                    }
                    const float3 sc = f3((float)sx + 0.5f, (float)sy + 0.5f, (float)sz + 0.5f);
                    if (s >= 0.999f) {
                        hit = true; th = tent + ts / fN; bc = base + sc / fN; bh = 0.5f / fN; bs = 1.f;
                    } else if (s > 0.03f) {
                        const float2 tt = iBox(pe - sc, rd, f3(0.5f * s));
                        if (tt.x <= tt.y && tt.y > 0.f) {
                            hit = true; th = tent + fmaxf(tt.x, 0.f) / fN; bc = base + sc / fN; bh = 0.5f * s / fN; bs = s;
                        }
                    }
                    if (hit) { lev = N == 4 ? 2 : 1; hx = cx; hy = cy; hz = cz; hdzw = dzw; hnf = nfd; break; }
                    if (smx < smy && smx < smz) { sx += stx; ts = smx; smx += tdl.x; if (sx < 0 || sx >= N) break; }
                    else if (smy < smz)          { sy += sty; ts = smy; smy += tdl.y; if (sy < 0 || sy >= N) break; }
                    else                         { sz += stz; ts = smz; smz += tdl.z; if (sz < 0 || sz >= N) break; }
                }
                if (hit) break;
            }
        }
        if (tmx < tmy && tmx < tmz) { cx += stx; tent = tmx; tmx += tdl.x; }
        else if (tmy < tmz)          { cy += sty; tent = tmy; tmy += tdl.y; }
        else                         { cz += stz; tent = tmz; tmz += tdl.z; }
        if (tent > P[63]) break;
    }

    const float pulse = P[36];
    const float3 fogc = srgb_to_linear(pal(pal_t + 0.55f, P + 13)) * (0.15f + 0.05f * pulse);
    float3 col = fogc;
    if (hit) {
        const float3 ph = ro + rd * th;
        const float3 q = (ph - bc) / bh;
        const float ax = fabsf(q.x), ay = fabsf(q.y), az = fabsf(q.z);
        const int a = (ax >= ay && ax >= az) ? 0 : (ay >= az ? 1 : 2);
        const float sn = cmp3(q, a) >= 0.f ? 1.f : -1.f;
        const int a1 = (a + 1) %% 3, a2 = (a + 2) %% 3;
        const float3 n = axis3(a, sn), t1 = axis3(a1, 1.f), t2 = axis3(a2, 1.f);
        const float u = clamp01(0.5f * (cmp3(q, a1) + 1.f)), v = clamp01(0.5f * (cmp3(q, a2) + 1.f));
        const float bw = P[37];
        float eu = sedge(u, bw), ev = sedge(v, bw);
        const float3 pc = ph - f3((float)hx, (float)hy, (float)hz);   // coarse-voxel local position 0..1
        const float u0 = clamp01(cmp3(pc, a1)), v0 = clamp01(cmp3(pc, a2));
        if (lev > 0) {                                                // sub-cube: octree grid lines fade in
            const float w1 = fminf(od, 1.f) * hnf, w2 = clamp01(od - 1.f) * hnf;
            float gu = pickmax(sedge(u0, bw), w1 * sedge(fract(2.f * u0), bw));
            float gv = pickmax(sedge(v0, bw), w1 * sedge(fract(2.f * v0), bw));
            gu = pickmax(gu, w2 * sedge(fract(4.f * u0), bw));
            gv = pickmax(gv, w2 * sedge(fract(4.f * v0), bw));
            const float wb = smooth(0.f, 0.1f, 1.f - bs);
            eu = mix(gu, eu, wb); ev = mix(gv, ev, wb);
        }
        const float3 nb = normalize(n + (t1 * eu + t2 * ev) * 0.9f);
        const float edge = fmaxf(fabsf(eu), fabsf(ev));

        // neighbour AO in the layer in front of the face (coarse voxels), plus emissive light bleed in-plane
        const int nx = hx + (a == 0 ? (int)sn : 0), ny = hy + (a == 1 ? (int)sn : 0), nz = hz + (a == 2 ? (int)sn : 0);
        const int d1x = a1 == 0, d1y = a1 == 1, d1z = a1 == 2, d2x = a2 == 0, d2y = a2 == 1, d2z = a2 == 2;
        float dz_;
        const float sp1 = cell_fill(nx + d1x, ny + d1y, nz + d1z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float sm1 = cell_fill(nx - d1x, ny - d1y, nz - d1z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float sp2 = cell_fill(nx + d2x, ny + d2y, nz + d2z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float sm2 = cell_fill(nx - d2x, ny - d2y, nz - d2z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float cpp = cell_fill(nx + d1x + d2x, ny + d1y + d2y, nz + d1z + d2z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float cpm = cell_fill(nx + d1x - d2x, ny + d1y - d2y, nz + d1z - d2z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float cmp = cell_fill(nx - d1x + d2x, ny - d1y + d2y, nz - d1z + d2z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float cmm = cell_fill(nx - d1x - d2x, ny - d1y - d2y, nz - d1z - d2z, P, dz_) > 0.5f ? 1.f : 0.f;
        const float au = u0 * u0, bu = (1.f - u0) * (1.f - u0), av = v0 * v0, bv = (1.f - v0) * (1.f - v0);
        float occ = sp1 * au + sm1 * bu + sp2 * av + sm2 * bv;
        occ += (cpp * (1.f - sp1) * (1.f - sp2) * au * av + cpm * (1.f - sp1) * (1.f - sm2) * au * bv +
                cmp * (1.f - sm1) * (1.f - sp2) * bu * av + cmm * (1.f - sm1) * (1.f - sm2) * bu * bv) * 1.5f;
        float ao = clamp01(1.f - 0.42f * occ);
        ao = mix(1.f, ao, 0.35f + 0.65f * bs);
        if (lev > 0 && fabsf(cmp3(pc, a) - (sn > 0.f ? 1.f : 0.f)) > 0.05f) ao *= 0.72f;   // face inside an octree hole

        const unsigned int izg = (unsigned int)(hz + (int)P[46]);
        float he;
        const float em = cell_emis(hx, hy, (int)izg, P, he);
        const float hc = hash1((unsigned int)hx + 0x7F4A7C15u, (unsigned int)hy * 0x94D049BBu, izg + (unsigned int)P[64]);
        const float pidx = pal_t + P[25] * 0.05f * hdzw + 0.28f * hc;
        const float3 alb = srgb_to_linear(pal(pidx, P + 13)) * 0.75f;
        const float w = wave_at(hdzw, P);

        const float tw = th * vs;
        const float att = 1.f / (1.f + (tw / P[70]) * (tw / P[70]));
        const float hd = saturate(dot(nb, -rd));
        const float kd = saturate(dot(nb, load3(P + 71)));
        float3 lit = alb * (P[48] * hd * att + 0.26f * kd + 0.05f) * ao;
        const float spec = powf(saturate(dot(reflect(rd, nb), -rd)), 24.f) * att * P[48] * 0.3f;
        lit += f3(spec) * ao;
        const float3 ecol = srgb_to_linear(pal(pidx + 0.33f, P + 13));
        lit += ecol * (edge * edge) * (0.05f + 1.1f * w) * (0.4f + 0.6f * att) * ao;   // bevel lines catch the wave
        lit *= 1.f + 0.45f * w;

        // emissive in-plane neighbours bleed onto this face
        if (P[69] > 0.f) {
            float h_;
            const float hp1 = cell_emis(hx + d1x, hy + d1y, (int)izg + d1z, P, h_);
            const float hm1 = cell_emis(hx - d1x, hy - d1y, (int)izg - d1z, P, h_);
            const float hp2 = cell_emis(hx + d2x, hy + d2y, (int)izg + d2z, P, h_);
            const float hm2 = cell_emis(hx - d2x, hy - d2y, (int)izg - d2z, P, h_);
            const float bl = hp1 * au * au + hm1 * bu * bu + hp2 * av * av + hm2 * bv * bv;
            if (bl > 0.f) {
                const float3 bcol = srgb_to_linear(pal(pal_t + P[25] * 0.05f * hdzw + 0.5f, P + 13));
                lit += bcol * (bl * P[69] * P[35] * (1.f + 1.5f * w) * (1.f - em));
            }
        }
        if (em > 0.f) {                                               // emissive voxel: a glowing panel in a bevel frame
            const float tw_ = 0.78f + 0.22f * sinf(P[60] + 6.2831853f * hc);
            const float panel = 0.3f + 0.7f * (1.f - edge);
            const float3 emc = srgb_to_linear(pal(pal_t + P[25] * 0.05f * hdzw + 0.5f + 0.3f * hc, P + 13));
            lit = mix(lit, emc * (P[35] * (1.f + 2.4f * w) * tw_ * panel) + lit * 0.3f, em);
        }
        col = fog_exp2(lit, fogc, tw, P[28]);
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"maxs": MAXS, "hn": HN}


class VoxelTunnel(Source):
    name = "voxel_tunnel"
    label = "Voxel tunnel"
    description = ("Fly down a carved tunnel of bevelled voxel cubes (round, square or cave) whose emissive "
                   "voxels flare as each beat rushes down the tube as a ring of light (3D voxel DDA).")
    tags = {"family": "retro", "dims": "3d", "toolkit": "sdf3d", "technique": ["raymarch", "tiling", "retro"],
            "moods": ["retro", "euphoric", "geometric"], "style": "wild", "energy": [0.4, 1.0]}
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.04),
        "emissive_ratio": num(0.0, 0.5, 0.14, "Emissive voxels", tier="regional", basic=True),
        "voxel_size": num(0.3, 1.2, 0.55, "Voxel size", tier="regional"),
        "tunnel_shape": num(0.0, 2.0, 0.7, "Tunnel shape (round > square > cave)", tier="regional"),
        "octree_depth": num(0.0, 2.0, 1.0, "Octree subdivision (near)", tier="regional"),
        "tunnel_radius": num(2.0, 8.0, 4.0, "Tunnel radius", tier="regional"),
        "ribs": num(0.0, 1.0, 0.35, "Ribs", tier="regional"),
        "bevel": num(0.0, 1.0, 0.55, "Bevel", tier="regional"),
        "fog": num(0.0, 0.1, 0.03, "Fog", step=0.001, tier="regional"),
        "travel_speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
        "roll_speed": num(-3.0, 3.0, 1.0, "Roll speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("voxel_tunnel")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        self.seed = int(params["seed"]) & 0xFFFF
        rng = np.random.default_rng(int(params["seed"]))
        self.cell_z = float(rng.uniform(0.0, 4096.0))   # flight position in voxels (host float64)
        self.pha = float(rng.uniform(0.0, 2 * math.pi))
        self.phb = float(rng.uniform(0.0, 2 * math.pi))
        self.phr = 0.0                                    # rib phase
        self.pz = 0.0                                     # palette phase along the tunnel (wraps at 1000)
        self.nz = float(rng.uniform(0.0, 512.0))          # noise z phase
        self.anim = 0.0                                   # carve-noise animation
        self.roll = 0.0
        self.twk = 0.0                                    # emissive twinkle
        self.sway = 0.0
        self.drift = 0.0
        self.e_s = None
        self.hist = np.zeros(HN, np.float32)
        self.hage = 0.0                                   # age of hist[0] (s)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else (1.0 if self.e_s is None else 0.0)
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        vs = float(p["voxel_size"])
        R = float(p["tunnel_radius"])
        shape = float(p["tunnel_shape"])
        sq = 1.0 - abs(min(max(shape, 0.0), 2.0) - 1.0)
        cave = min(max(shape - 1.0, 0.0), 1.0)
        period = float(p["palette_period"])

        # ---- integrate rates (pulse adds to rates only)
        v = float(p["travel_speed"]) * spd * (2.2 + 12.0 * e + 5.0 * pulse) * math.sqrt(R / 4.0)
        dz = v * dt
        self.cell_z += dz / vs
        if self.cell_z > IZ_WRAP:
            self.cell_z -= IZ_WRAP
        ka = 2 * math.pi / (7.0 * R)
        kb = 0.71 * ka
        kr = 2 * math.pi / (1.3 * R)
        nf = 1.0 / (0.45 * R + 0.6)
        tau = 2 * math.pi
        self.pha = wrap(self.pha + ka * dz, tau)
        self.phb = wrap(self.phb + kb * dz, tau)
        self.phr = wrap(self.phr + kr * dz, tau)
        self.pz = wrap(self.pz + period * 0.05 * dz, 1000.0)
        self.nz = wrap(self.nz + nf * dz, 65536.0)
        self.anim = wrap(self.anim + spd * (0.04 + 0.5 * e ** 1.2) * dt, 4096.0)
        self.roll = wrap(self.roll + float(p["roll_speed"]) * spd * (0.03 + 0.3 * e + 0.12 * pulse) * dt, tau)
        self.twk = wrap(self.twk + spd * (0.7 + 2.0 * e) * dt, tau)
        self.sway += spd * (0.10 + 0.25 * es) * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- pulse history (newest first); the kernel reads it by depth / wave speed
        self.hage += dt
        pushes = 0
        while self.hage >= HDT and pushes < HN:
            self.hist[1:] = self.hist[:-1]
            self.hist[0] = pulse
            self.hage -= HDT
            pushes += 1
        if pushes >= HN:
            self.hage = min(self.hage, HDT)

        # ---- camera: on the curving path, swaying, looking ahead, rolling
        ax, ay = 0.45 * R, 0.30 * R

        def path(d: float) -> tuple[float, float]:
            return ax * math.sin(ka * d + self.pha), ay * math.sin(kb * d + self.phb)

        sw = 0.08 * R * (0.3 + 0.7 * es)
        sx, sy = sw * math.sin(self.sway), sw * math.cos(0.77 * self.sway + 0.6)
        ex, ey = path(0.0)
        look = 1.6 * R
        tx, ty = path(look)
        iz0 = math.floor(self.cell_z)
        fz = self.cell_z - iz0
        eye = ((ex + sx) / vs, (ey + sy) / vs, fz)
        tgt = ((tx + 0.3 * sx) / vs, (ty + 0.3 * sy) / vs, fz + look / vs)
        fov = 72.0 + 8.0 * es + 3.0 * pulse
        roll = self.roll + 0.06 * math.sin(0.53 * self.sway)

        reach = 0.8 * MAXS * vs                            # world depth the DDA can cover along the axis
        fog = max(float(p["fog"]), 1.7 / reach)
        amp = 0.9 * vs + cave * 0.8 * R
        jit = 0.55 * vs
        band = 0.85 * vs
        pop_max = 0.9 * vs
        rib_d = float(p["ribs"]) * 0.3 * R
        core0, core1 = 0.28 * R, 0.42 * R
        inner = max(R - rib_d - amp - jit - pop_max - band, core0)
        outer = R + amp + jit + band

        Ph = self.Ph
        Ph[0:13] = camera_pack(eye, tgt, (0.0, 1.0, 0.0), fov_deg=fov, roll=roll)
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = period
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * (1.0 + 0.12 * pulse)
        Ph[28] = fog
        Ph[29] = vs
        Ph[30] = R
        Ph[31] = sq
        Ph[32] = amp
        Ph[33] = self.anim
        Ph[34] = float(p["emissive_ratio"]) * (0.35 + 1.0 * es ** 1.3)
        Ph[35] = 1.6 * (0.55 + 0.45 * e) * (1.0 + 0.3 * pulse)
        Ph[36] = pulse
        Ph[37] = 0.03 + 0.17 * float(p["bevel"])
        Ph[38] = float(p["octree_depth"])
        Ph[39] = 2.2 * R + 4.0
        Ph[40:46] = (ax, ka, self.pha, ay, kb, self.phb)
        Ph[46] = float(iz0)
        Ph[47] = WAVE_C
        Ph[48] = 1.5 + 0.7 * e
        Ph[49] = band
        Ph[50] = nf
        Ph[51] = self.nz
        Ph[52] = jit
        Ph[53] = inner
        Ph[54] = outer
        Ph[55] = self.pz
        Ph[56] = rib_d
        Ph[57] = kr
        Ph[58] = self.phr
        Ph[59] = pulse * pop_max
        Ph[60] = self.twk
        Ph[61] = HDT
        Ph[62] = self.hage
        Ph[63] = 1.3 * reach / vs
        Ph[64] = float(self.seed)
        Ph[65] = 0.42
        Ph[66] = core0
        Ph[67] = core1
        Ph[68] = cave
        Ph[69] = 0.35
        Ph[70] = 0.9 * R + 4.0
        kx, ky, kz = 0.35, 0.8, -0.45
        kn = math.sqrt(kx * kx + ky * ky + kz * kz)
        Ph[71:74] = (kx / kn, ky / kn, kz / kn)
        Ph[80:80 + HN] = self.hist
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
