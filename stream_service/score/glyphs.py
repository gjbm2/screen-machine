"""Leipzig (SMuFL) glyphs drawn by the GPU: the score_v2 scene's notes as real notation from the first frame.

The outlines are Verovio's own copy of the font (verovio/data/Leipzig/<code>.xml: one SVG path per glyph, 1000 units
an em = 4 staff spaces, y up) with its metrics (Leipzig.xml: bounding boxes and SMuFL anchors), the same shapes the
engraver draws, so a glyph note at an engraved head's origin covers that head's ink pixel for pixel.  Each glyph's
exact signed distance (staff spaces, negative inside, nonzero winding as resvg fills) is baked once on the GPU into an
atlas (TPS texels a staff space, PAD of margin): a few ms in setup.

Device code (GLYPH_SRC): ``glyph_pass`` draws up to MAXN records, x-sorted by their box's left edge (a 7-step search
finds the first that may reach a pixel).  A record is a notehead (two glyphs morphing), its stem (a vertical box: also
a bar line or a tick with no head), ledger lines, an accidental (scaled about its centre: pops), a colour mix and a
halo around the head.  Coverage is clamp(0.5 - d): resvg's box-filter edge, so the weight matches the ink.

Host: ``layout()`` (the atlas table), ``device_atlas(cp)`` (baked), ``boxes_sort(R, n, sp, halo_sp, N, U)`` (boxes and
the sort, vectorised).  Source: Original (the outlines are the Leipzig font's, SIL OFL 1.1, read from Verovio's data).
License: MIT.
"""
from __future__ import annotations

import pathlib
import re

import numpy as np

TPS = 32.0                  # atlas texels a staff space
PAD = 0.6                   # margin around each glyph (sp)
UNITS_PER_SP = 250.0

GLYPHS = [
    ("noteheadBlack", "E0A4"), ("noteheadHalf", "E0A3"), ("noteheadWhole", "E0A2"),
    ("accidentalSharp", "E262"), ("accidentalFlat", "E260"), ("accidentalNatural", "E261"),
    ("accidentalDoubleSharp", "E263"), ("accidentalDoubleFlat", "E264"),
] + [(f"timeSig{d}", f"E08{d}") for d in range(10)]
GI = {name: i for i, (name, _c) in enumerate(GLYPHS)}
CODE_GI = {int(c, 16): i for i, (_n, c) in enumerate(GLYPHS)}
BLACK, HALF, WHOLE = GI["noteheadBlack"], GI["noteheadHalf"], GI["noteheadWhole"]
SHARP, FLAT, NATURAL, DSHARP, DFLAT = (GI[n] for n in ("accidentalSharp", "accidentalFlat", "accidentalNatural",
                                                      "accidentalDoubleSharp", "accidentalDoubleFlat"))
TS0 = GI["timeSig0"]
ACC_BY_ALT = {1: SHARP, -1: FLAT, 0: NATURAL, 2: DSHARP, -2: DFLAT}

# record layout (floats)
NF, NU = 32, 16
MAXN = 384
STEM_HW, LEDGER_HT, LEDGER_EXT = 0.05, 0.061, 0.267        # sp: Verovio's stem 18/180, ledger 22/180, ext 48/180
BAR_HW = 0.075                                              # a bar line: 27/180

GLYPH_SRC = r"""
#define G_NF """ + str(NF) + r"""
#define G_TPS """ + f"{TPS:.1f}f" + r"""
#define G_LEDGER_HT """ + f"{LEDGER_HT}f" + r"""
#define G_FAR 1.0e4f

// a glyph's signed distance (sp) and gradient (per sp) at (u, v) sp from its origin; G_FAR outside its rect
__device__ __forceinline__ float3 glyph_sd(const float* __restrict__ A, const int AW, const float* __restrict__ GT,
                                          const int g, const float u, const float v)
{
    const float* T = GT + 12 * g;
    const float tx = T[4] + u * G_TPS - 0.5f, ty = T[5] + v * G_TPS - 0.5f;
    if (tx < 0.f || ty < 0.f || tx >= T[2] - 1.f || ty >= T[3] - 1.f) return make_float3(G_FAR, 0.f, 0.f);
    const int ix = (int)tx, iy = (int)ty;
    const float fx = tx - ix, fy = ty - iy;
    const float* a = A + ((int)T[1] + iy) * AW + (int)T[0] + ix;
    const float c00 = __ldg(a), c10 = __ldg(a + 1), c01 = __ldg(a + AW), c11 = __ldg(a + AW + 1);
    const float top = c00 + (c10 - c00) * fx, bot = c01 + (c11 - c01) * fx;
    return make_float3(top + (bot - top) * fy,
                       ((c10 - c00) * (1.f - fy) + (c11 - c01) * fy) * G_TPS,
                       (bot - top) * G_TPS);
}

__device__ __forceinline__ float sd_px(const float3 s, const float sx, const float sy) {
    if (s.x >= G_FAR) return G_FAR;
    const float gx = s.y / sx, gy = s.z / sy;
    return s.x * rsqrtf(fmaxf(gx * gx + gy * gy, 1e-6f));
}

__device__ __forceinline__ float box_d(const float dx, const float dy) {
    return dx > 0.f && dy > 0.f ? sqrtf(dx * dx + dy * dy) : fmaxf(dx, dy);
}

// R, one record (G_NF floats):
//  [0] ox [1] oy  head glyph origin (px)   [2] sx [3] sy px per staff space   [4] glyph A (-1 none) [5] glyph B
//  [6] morph A -> B   [7] alpha   [8] stem x (px, centre) [9] stem y0 [10] stem y1 [11] stem half width (px, 0 none)
//  [12] accidental glyph (-1 none) [13] its centre x (px) [14] its scale   [15] first ledger y [16] ledger step
//  [17] ledger count   [18] colour mix (ink .. accent) [19] halo   [20..22] accent rgb   [23..26] box x0 y0 x1 y1
//  [27] ledger x0 [28] ledger x1 [29] accidental y (px)
__device__ __forceinline__ void glyph_dist(const float* __restrict__ Q, const float X, const float Y,
                                           const float* __restrict__ A, const int AW, const float* __restrict__ GT,
                                           const float sp, float& d, float& dh)
{
    d = G_FAR; dh = G_FAR;
    const int ga = (int)Q[4], gb = (int)Q[5];
    const float sx = Q[2], sy = Q[3];
    if (ga >= 0) {
        const float u = (X - Q[0]) / sx, v = (Y - Q[1]) / sy;
        float3 s = glyph_sd(A, AW, GT, ga, u, v);
        const float mt = Q[6];
        if (mt > 0.f && gb >= 0 && gb != ga) {
            const float3 t = glyph_sd(A, AW, GT, gb, u, v);
            if (s.x >= G_FAR || t.x >= G_FAR) s = (mt < 0.5f) ? s : t;
            else s = make_float3(s.x + (t.x - s.x) * mt, s.y + (t.y - s.y) * mt, s.z + (t.z - s.z) * mt);
        }
        dh = sd_px(s, sx, sy);
        d = dh;
    }
    if (Q[11] > 0.f) {
        const float y0 = fminf(Q[9], Q[10]), y1 = fmaxf(Q[9], Q[10]);
        d = fminf(d, box_d(fabsf(X - Q[8]) - Q[11], fmaxf(y0 - Y, Y - y1)));
    }
    const int gacc = (int)Q[12];
    if (gacc >= 0 && Q[14] > 0.01f) {
        const float k = Q[14], adv = GT[12 * gacc + 10];
        d = fminf(d, sd_px(glyph_sd(A, AW, GT, gacc, (X - Q[13]) / (sp * k) + 0.5f * adv, (Y - Q[29]) / (sp * k)),
                           sp * k, sp * k));
    }
    const int nl = (int)Q[17];
    if (nl > 0) {
        const float ly0 = Q[15], ldy = Q[16];
        const float k = fminf(fmaxf(rintf((Y - ly0) / ldy), 0.f), (float)(nl - 1));
        d = fminf(d, box_d(fmaxf(Q[27] - X, X - Q[28]), fabsf(Y - (ly0 + k * ldy)) - G_LEDGER_HT * sp));
    }
}

__device__ __forceinline__ void glyph_paint(float3& c, const float d, const float dh, const float alpha,
                                            const float mix, const float halo, const float3 acc, const float3 inkc,
                                            const float R, const int night)
{
    if (halo > 0.f && dh < 2.5f * R) {
        const float g = halo * alpha * __expf(-fmaxf(dh, 0.f) / R) * (1.f - smooth(1.5f * R, 2.5f * R, dh));
        if (!night) { c.x += (acc.x * 0.8f - c.x) * g * 0.6f; c.y += (acc.y * 0.8f - c.y) * g * 0.6f;
                      c.z += (acc.z * 0.8f - c.z) * g * 0.6f; }
        else { c.x += acc.x * g; c.y += acc.y * g; c.z += acc.z * g; }
    }
    const float cov = clamp01(0.5f - d) * alpha;
    if (cov > 0.f) {
        const float k = night ? 1.f : 0.55f;                // on paper a coloured ink is a dark one
        const float3 col = make_float3(inkc.x + (acc.x * k - inkc.x) * mix, inkc.y + (acc.y * k - inkc.y) * mix,
                                       inkc.z + (acc.z * k - inkc.z) * mix);
        c.x += (col.x - c.x) * cov; c.y += (col.y - c.y) * cov; c.z += (col.z - c.z) * cov;
    }
}

// U: [0] records [2..5] bbox x0 y0 x1 y1 [6] widest box (px) [7] staff space (px) [8] halo radius (sp)
__device__ __forceinline__ void glyph_pass(float3& c, const float X, const float Y, const float* __restrict__ N,
                                           const float* __restrict__ U, const float* __restrict__ A, const int AW,
                                           const float* __restrict__ GT, const float3 inkc, const int night)
{
    const int n = (int)U[0];
    if (n <= 0 || X < U[2] || Y < U[3] || X > U[4] || Y > U[5]) return;
    const float sp = U[7], R = fmaxf(U[8] * sp, 0.5f);
    int lo = 0, hi = n;
    const float xmin = X - U[6];
    for (int s = 0; s < 9 && lo < hi; ++s) {
        const int mid = (lo + hi) >> 1;
        if (N[G_NF * mid + 23] < xmin) lo = mid + 1; else hi = mid;
    }
    for (int i = lo; i < n; ++i) {
        const float* Q = N + G_NF * i;
        if (Q[23] > X) break;
        if (X > Q[25] || Y < Q[24] || Y > Q[26] || Q[7] <= 0.002f) continue;
        float d, dh;
        glyph_dist(Q, X, Y, A, AW, GT, sp, d, dh);
        glyph_paint(c, d, dh, Q[7], Q[18], Q[19], make_float3(Q[20], Q[21], Q[22]), inkc, R, night);
    }
}
"""


_TOK = re.compile(r"[MmLlHhVvCcSsQqTtZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _verovio_data() -> pathlib.Path:
    import verovio
    return pathlib.Path(verovio.__file__).resolve().parent / "data"


def _cubic(p0, p1, p2, p3, n=24):
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3 * p3


def _quad(p0, p1, p2, n=16):
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return ((1 - t) ** 2) * p0 + 2 * (1 - t) * t * p1 + t * t * p2


def parse_path(d: str) -> list[np.ndarray]:
    """SVG path data -> closed polylines (font units, as written)."""
    toks = _TOK.findall(d)
    i = 0
    cmd = None
    cur = np.zeros(2)
    start = np.zeros(2)
    last_c = last_q = None
    contours: list = []
    pts: list = []

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def close():
        nonlocal pts
        if len(pts) > 1:
            contours.append(np.array(pts))
        pts = []

    while i < len(toks):
        if re.fullmatch(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                close()
                cur = start.copy()
                last_c = last_q = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        base = cur if rel else np.zeros(2)
        if c == "M":
            close()
            cur = base + np.array([num(), num()])
            start = cur.copy()
            pts = [cur.copy()]
            cmd = "l" if rel else "L"
            last_c = last_q = None
        elif c == "L":
            cur = base + np.array([num(), num()])
            pts.append(cur.copy())
            last_c = last_q = None
        elif c == "H":
            x = num()
            cur = np.array([cur[0] + x if rel else x, cur[1]])
            pts.append(cur.copy())
            last_c = last_q = None
        elif c == "V":
            y = num()
            cur = np.array([cur[0], cur[1] + y if rel else y])
            pts.append(cur.copy())
            last_c = last_q = None
        elif c == "C":
            p1 = base + np.array([num(), num()])
            p2 = base + np.array([num(), num()])
            p3 = base + np.array([num(), num()])
            pts.extend(_cubic(cur, p1, p2, p3))
            last_c, last_q = p2, None
            cur = p3
        elif c == "S":
            p1 = 2 * cur - last_c if last_c is not None else cur.copy()
            p2 = base + np.array([num(), num()])
            p3 = base + np.array([num(), num()])
            pts.extend(_cubic(cur, p1, p2, p3))
            last_c, last_q = p2, None
            cur = p3
        elif c == "Q":
            p1 = base + np.array([num(), num()])
            p2 = base + np.array([num(), num()])
            pts.extend(_quad(cur, p1, p2))
            last_q, last_c = p1, None
            cur = p2
        elif c == "T":
            p1 = 2 * cur - last_q if last_q is not None else cur.copy()
            p2 = base + np.array([num(), num()])
            pts.extend(_quad(cur, p1, p2))
            last_q, last_c = p1, None
            cur = p2
        else:
            raise ValueError(f"path command {cmd}")
    close()
    return contours


def glyph_outline(code: str) -> list[np.ndarray]:
    """The glyph's contours in staff spaces, y DOWN, origin at the SMuFL origin."""
    txt = (_verovio_data() / "Leipzig" / f"{code}.xml").read_text()
    d = re.search(r'\sd="([^"]+)"', txt).group(1)
    return [np.stack([c[:, 0] / UNITS_PER_SP, -c[:, 1] / UNITS_PER_SP], axis=1) for c in parse_path(d)]


def metrics() -> dict:
    """code -> {bbox (x, y, w, h) sp (y up), anchors {name: (x, y)} sp (y up)}."""
    txt = (_verovio_data() / "Leipzig.xml").read_text()
    out = {}
    for m in re.finditer(r'<g c="([0-9A-F]+)" x="([-\d.]+)" y="([-\d.]+)" w="([-\d.]+)" h="([-\d.]+)"[^>]*?(/>|>(.*?)</g>)',
                         txt, re.S):
        bb = tuple(float(m.group(k)) / UNITS_PER_SP for k in (2, 3, 4, 5))
        anchors = {a.group(1): (float(a.group(2)), float(a.group(3)))
                   for a in re.finditer(r'<a n="(\w+)" x="([-\d.]+)" y="([-\d.]+)"', m.group(7) or "")}
        out[m.group(1)] = {"bbox": bb, "anchors": anchors}
    return out


def layout(tps: float = TPS, pad: float = PAD):
    """(table float32 [NG, 12], AH, AW, outlines).  Table per glyph: [0] atlas x0 [1] y0 [2] w [3] h (texels)
    [4] origin x [5] origin y (texels from the rect's corner) [6, 7] stemUpSE (sp, y down) [8, 9] stemDownNW
    [10] advance (sp) [11] bbox x0 (sp)."""
    met = metrics()
    rects, rows, outlines = [], [], []
    for _name, code in GLYPHS:
        cs = glyph_outline(code)
        allp = np.concatenate(cs)
        x0, y0 = allp.min(0) - pad
        x1, y1 = allp.max(0) + pad
        w, h = int(np.ceil((x1 - x0) * tps)) + 1, int(np.ceil((y1 - y0) * tps)) + 1
        an = met.get(code, {}).get("anchors", {})
        su = an.get("stemUpSE", (0.0, 0.0))
        sd = an.get("stemDownNW", (0.0, 0.0))
        bb = met.get(code, {}).get("bbox", (0, 0, 0, 0))
        rects.append((w, h))
        rows.append((-x0 * tps, -y0 * tps, su[0], -su[1], sd[0], -sd[1], bb[0] + bb[2], bb[0]))
        outlines.append(cs)
    AW = 1024
    order = sorted(range(len(rects)), key=lambda i: -rects[i][1])
    pos = [None] * len(rects)
    x = y = row_h = 0
    for i in order:
        w, h = rects[i]
        if x + w > AW:
            x, y, row_h = 0, y + row_h + 1, 0
        pos[i] = (x, y)
        x += w + 1
        row_h = max(row_h, h)
    AH = y + row_h + 1
    table = np.zeros((len(GLYPHS), 12), np.float32)
    for i, (ox, oy, sux, suy, sdx, sdy, adv, bx0) in enumerate(rows):
        table[i] = (pos[i][0], pos[i][1], rects[i][0], rects[i][1], ox, oy, sux, suy, sdx, sdy, adv, bx0)
    return table, AH, AW, outlines


_CENT = None


def centres() -> np.ndarray:
    """Per glyph (NG x 2): its box's centre from its origin (sp, x right, y down): the engraver reports centres."""
    global _CENT
    if _CENT is None:
        met = metrics()
        c = np.zeros((len(GLYPHS), 2))
        for i, (_n, code) in enumerate(GLYPHS):
            bx, by, bw, bh = met.get(code, {}).get("bbox", (0.0, 0.0, 0.0, 0.0))
            c[i] = (bx + 0.5 * bw, -(by + 0.5 * bh))
        _CENT = c
    return _CENT


BAKE_SRC = r"""
extern "C" __global__
void bake_sdf(float* __restrict__ atlas, const int AW, const float4* __restrict__ seg, const int s0, const int ns,
              const int ax0, const int ay0, const int w, const int h, const float x0, const float y0, const float tps,
              const float pad)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= w || j >= h) return;
    const float px = x0 + (i + 0.5f) / tps, py = y0 + (j + 0.5f) / tps;
    float best = 1e30f;
    int wind = 0;
    for (int k = s0; k < s0 + ns; ++k) {
        const float4 s = seg[k];
        const float abx = s.z - s.x, aby = s.w - s.y, apx = px - s.x, apy = py - s.y;
        const float l2 = fmaxf(abx * abx + aby * aby, 1e-18f);
        const float t = fminf(fmaxf((apx * abx + apy * aby) / l2, 0.f), 1.f);
        const float qx = apx - t * abx, qy = apy - t * aby;
        best = fminf(best, qx * qx + qy * qy);
        const float cr = abx * apy - apx * aby;
        if (s.y <= py && s.w > py && cr > 0.f) ++wind;
        else if (s.y > py && s.w <= py && cr < 0.f) --wind;
    }
    const float d = sqrtf(best);
    atlas[(ay0 + j) * AW + ax0 + i] = wind != 0 ? -d : fminf(d, pad);
}
"""

_CACHE: dict = {}


def device_atlas(cp):
    """(atlas device array AH x AW float32, glyph table device array, table host array): baked once per device."""
    dev = cp.cuda.Device().id
    hit = _CACHE.get(dev)
    if hit is not None:
        return hit
    table, AH, AW, outlines = layout()
    segs = []
    for cs in outlines:
        segs.append(np.concatenate([np.concatenate([c, np.roll(c, -1, axis=0)], 1) for c in cs]).astype(np.float32))
    allseg = cp.asarray(np.concatenate(segs))
    atlas = cp.full((AH, AW), PAD, cp.float32)
    k = cp.RawModule(code=BAKE_SRC).get_function("bake_sdf")
    s0 = 0
    for g, sg in enumerate(segs):
        ax0, ay0, w, h, ox, oy = (int(table[g, 0]), int(table[g, 1]), int(table[g, 2]), int(table[g, 3]),
                                  float(table[g, 4]), float(table[g, 5]))
        k(((w + 15) // 16, (h + 15) // 16, 1), (16, 16, 1),
          (atlas, np.int32(AW), allseg, np.int32(s0), np.int32(len(sg)), np.int32(ax0), np.int32(ay0), np.int32(w),
           np.int32(h), np.float32(-ox / TPS), np.float32(-oy / TPS), np.float32(TPS), np.float32(PAD)))
        s0 += len(sg)
    out = (atlas, cp.asarray(table), table)
    _CACHE[dev] = out
    return out


def boxes_sort(R: np.ndarray, n: int, sp: float, halo_sp: float, N: np.ndarray, U: np.ndarray,
               adv: np.ndarray) -> int:
    """Records R[:n] (fields 0..22, 27..29 set) -> their boxes (23..26), sorted by x0 into N, the uniforms U.
    ``adv``: the table's advance per glyph (sp).  Whole-array numpy, no per-record Python."""
    n = min(n, MAXN)
    U[:] = 0.0
    U[7], U[8] = sp, halo_sp
    if n <= 0:
        return 0
    Q = R[:n]
    BIG = 1e9
    pad = (PAD + 2.5 * halo_sp) * sp + 2.0
    ga = Q[:, 4]
    has_g = ga >= 0
    ox, oy, sx, sy = Q[:, 0], Q[:, 1], np.abs(Q[:, 2]), np.abs(Q[:, 3])
    gadv = adv[np.maximum(ga, 0).astype(np.intp)]
    x0 = np.where(has_g, ox - 0.1 * sx, BIG)
    x1 = np.where(has_g, ox + (gadv + 0.2) * sx, -BIG)
    y0 = np.where(has_g, oy - 2.2 * sy, BIG)
    y1 = np.where(has_g, oy + 2.2 * sy, -BIG)
    st = Q[:, 11] > 0
    if st.any():
        x0 = np.where(st, np.minimum(x0, Q[:, 8] - Q[:, 11]), x0)
        x1 = np.where(st, np.maximum(x1, Q[:, 8] + Q[:, 11]), x1)
        y0 = np.where(st, np.minimum(y0, np.minimum(Q[:, 9], Q[:, 10])), y0)
        y1 = np.where(st, np.maximum(y1, np.maximum(Q[:, 9], Q[:, 10])), y1)
    ha = (Q[:, 12] >= 0) & (Q[:, 14] > 0.01)
    if ha.any():
        r = 1.2 * sp * Q[:, 14]
        x0 = np.where(ha, np.minimum(x0, Q[:, 13] - r), x0)
        x1 = np.where(ha, np.maximum(x1, Q[:, 13] + r), x1)
        y0 = np.where(ha, np.minimum(y0, Q[:, 29] - 2.0 * sp), y0)
        y1 = np.where(ha, np.maximum(y1, Q[:, 29] + 2.0 * sp), y1)
    hl = Q[:, 17] > 0
    if hl.any():
        lend = Q[:, 15] + np.maximum(Q[:, 17] - 1, 0) * Q[:, 16]
        x0 = np.where(hl, np.minimum(x0, Q[:, 27]), x0)
        x1 = np.where(hl, np.maximum(x1, Q[:, 28]), x1)
        y0 = np.where(hl, np.minimum(y0, np.minimum(Q[:, 15], lend)), y0)
        y1 = np.where(hl, np.maximum(y1, np.maximum(Q[:, 15], lend)), y1)
    bad = x0 > x1
    if bad.any():
        Q[bad, 7] = 0.0
        x0[bad] = x1[bad] = y0[bad] = y1[bad] = 0.0
    Q[:, 23], Q[:, 24], Q[:, 25], Q[:, 26] = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    np.take(Q, np.argsort(Q[:, 23], kind="stable"), axis=0, out=N[:n])
    U[0] = n
    U[2], U[3], U[4], U[5] = Q[:, 23].min(), Q[:, 24].min(), Q[:, 25].max(), Q[:, 26].max()
    U[6] = float((Q[:, 25] - Q[:, 23]).max())
    return n
