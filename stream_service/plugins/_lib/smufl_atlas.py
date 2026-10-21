"""Leipzig (SMuFL) glyphs as a signed-distance atlas, baked once per process on the GPU.

(Would live at stream_service/plugins/_lib/smufl_atlas.py.  Adapted from the glyph spike's smufl.py + smufl_gpu.py.)

The outlines come from Verovio's own copy of the Leipzig font (verovio/data/Leipzig/<code>.xml: one SVG path per
glyph, 1000 units per em = 4 staff spaces, y up) and its metrics file (Leipzig.xml: bounding boxes and SMuFL anchors
in staff spaces).  Each glyph's exact signed distance (staff spaces, negative inside, nonzero winding) is sampled on a
TPS-texels-per-staff-space grid with PAD staff spaces of margin, one GPU thread per texel (~0.1 s warm).

Table per glyph (float32 [NG, 12]): [0] atlas x0 [1] y0 [2] w [3] h (texels) [4] origin x [5] origin y (texels from
the rect's corner) [6, 7] stemUpSE (sp, y down) [8, 9] stemDownNW (sp, y down) [10] advance (sp) [11] 0.
Fonts: Leipzig, SIL OFL 1.1 (shipped with Verovio, LGPL-3.0-or-later, read as data).
"""
from __future__ import annotations

import pathlib
import re
import threading

import numpy as np

TPS = 32.0          # atlas texels per staff space
PAD = 0.9           # margin around each glyph (sp)
UNITS_PER_SP = 250.0

GLYPHS = [
    ("noteheadBlack", "E0A4"), ("noteheadHalf", "E0A3"), ("noteheadWhole", "E0A2"),
    ("accidentalSharp", "E262"), ("accidentalFlat", "E260"), ("accidentalNatural", "E261"),
    ("flag8thUp", "E240"), ("flag8thDown", "E241"), ("flag16thUp", "E242"), ("flag16thDown", "E243"),
    ("gClef", "E050"), ("fClef", "E062"), ("brace", "E000"), ("augmentationDot", "E1E7"),
] + [(f"timeSig{d}", f"E08{d}") for d in range(10)]
GI = {name: i for i, (name, _c) in enumerate(GLYPHS)}

_TOK = re.compile(r"[MmLlHhVvCcSsQqTtZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def verovio_data() -> pathlib.Path:
    import verovio
    return pathlib.Path(verovio.__file__).resolve().parent / "data"


def _cubic(p0, p1, p2, p3, n=24):
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return ((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * p1 + 3 * (1 - t) * t * t * p2 + t ** 3 * p3


def _quad(p0, p1, p2, n=16):
    t = np.linspace(0.0, 1.0, n + 1)[1:, None]
    return ((1 - t) ** 2) * p0 + 2 * (1 - t) * t * p1 + t * t * p2


def parse_path(d: str) -> list:
    """SVG path data -> closed polylines (font units, y up as written)."""
    toks = _TOK.findall(d)
    i, cmd = 0, None
    cur, start = np.zeros(2), np.zeros(2)
    last_c = last_q = None
    contours, pts = [], []

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
            p1, p2, p3 = (base + np.array([num(), num()]) for _ in range(3))
            pts.extend(_cubic(cur, p1, p2, p3))
            last_c, last_q, cur = p2, None, p3
        elif c == "S":
            p1 = 2 * cur - last_c if last_c is not None else cur.copy()
            p2, p3 = (base + np.array([num(), num()]) for _ in range(2))
            pts.extend(_cubic(cur, p1, p2, p3))
            last_c, last_q, cur = p2, None, p3
        elif c == "Q":
            p1, p2 = (base + np.array([num(), num()]) for _ in range(2))
            pts.extend(_quad(cur, p1, p2))
            last_q, last_c, cur = p1, None, p2
        elif c == "T":
            p1 = 2 * cur - last_q if last_q is not None else cur.copy()
            p2 = base + np.array([num(), num()])
            pts.extend(_quad(cur, p1, p2))
            last_q, last_c, cur = p1, None, p2
        else:
            raise ValueError(f"path command {cmd}")
    close()
    return contours


def glyph_outline(code: str, data: pathlib.Path) -> list:
    """The glyph's contours in staff spaces, y DOWN, origin at the SMuFL origin."""
    txt = (data / "Leipzig" / f"{code}.xml").read_text()
    d = re.search(r'\sd="([^"]+)"', txt).group(1)
    return [np.stack([c[:, 0] / UNITS_PER_SP, -c[:, 1] / UNITS_PER_SP], axis=1) for c in parse_path(d)]


def metrics(data: pathlib.Path) -> dict:
    txt = (data / "Leipzig.xml").read_text()
    out = {}
    for m in re.finditer(r'<g c="([0-9A-F]+)" x="([-\d.]+)" y="([-\d.]+)" w="([-\d.]+)" h="([-\d.]+)"[^>]*?(/>|>(.*?)</g>)',
                         txt, re.S):
        bb = tuple(float(m.group(k)) / UNITS_PER_SP for k in (2, 3, 4, 5))
        anchors = {a.group(1): (float(a.group(2)), float(a.group(3)))
                   for a in re.finditer(r'<a n="(\w+)" x="([-\d.]+)" y="([-\d.]+)"', m.group(7) or "")}
        out[m.group(1)] = {"bbox": bb, "anchors": anchors}
    return out


def layout(data: pathlib.Path, tps: float = TPS, pad: float = PAD):
    """(table float32 [NG, 12], AH, AW, outlines)."""
    met = metrics(data)
    rects, rows, outlines = [], [], []
    for _name, code in GLYPHS:
        cs = glyph_outline(code, data)
        allp = np.concatenate(cs)
        x0, y0 = allp.min(0) - pad
        x1, y1 = allp.max(0) + pad
        w, h = int(np.ceil((x1 - x0) * tps)) + 1, int(np.ceil((y1 - y0) * tps)) + 1
        an = met.get(code, {}).get("anchors", {})
        su = an.get("stemUpSE", (0.0, 0.0))
        sd = an.get("stemDownNW", (0.0, 0.0))
        bb = met.get(code, {}).get("bbox", (0, 0, 0, 0))
        rects.append((w, h))
        rows.append((-x0 * tps, -y0 * tps, su[0], -su[1], sd[0], -sd[1], bb[0] + bb[2]))
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
    for i, (ox, oy, sux, suy, sdx, sdy, adv) in enumerate(rows):
        table[i] = (pos[i][0], pos[i][1], rects[i][0], rects[i][1], ox, oy, sux, suy, sdx, sdy, adv, 0.0)
    return table, AH, AW, outlines


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
    const float d = fminf(sqrtf(best), pad);
    atlas[(ay0 + j) * AW + ax0 + i] = wind != 0 ? -sqrtf(best) : d;
}
"""

_cache: dict = {}
_lock = threading.Lock()


def atlas(cp, raw_module) -> tuple:
    """(atlas device float32 [AH, AW] in sp, table numpy [NG, 12]) for the current device, baked once per process.
    Raises if Verovio's font data isn't there (the caller draws without glyphs)."""
    dev = cp.cuda.runtime.getDevice()
    with _lock:
        hit = _cache.get(dev)
        if hit is not None:
            return hit
        data = verovio_data()
        table, AH, AW, outlines = layout(data)
        segs = []
        for cs in outlines:
            parts = [np.concatenate([c, np.roll(c, -1, axis=0)], 1) for c in cs]
            segs.append(np.concatenate(parts).astype(np.float32))
        allseg = cp.asarray(np.concatenate(segs))
        a = cp.full((AH, AW), PAD, cp.float32)
        k = raw_module(BAKE_SRC).get_function("bake_sdf")
        s0 = 0
        for g, sg in enumerate(segs):
            ax0, ay0, w, h, ox, oy = (int(table[g, 0]), int(table[g, 1]), int(table[g, 2]), int(table[g, 3]),
                                      float(table[g, 4]), float(table[g, 5]))
            k(((w + 15) // 16, (h + 15) // 16, 1), (16, 16, 1),
              (a, np.int32(AW), allseg, np.int32(s0), np.int32(len(sg)), np.int32(ax0), np.int32(ay0), np.int32(w),
               np.int32(h), np.float32(-ox / TPS), np.float32(-oy / TPS), np.float32(TPS), np.float32(PAD)))
            s0 += len(sg)
        _cache[dev] = (a, table)
        return a, table
