"""score_v2's shared pieces, no GPU: the glyph table and boxes (score/glyphs.py) and the note rows' motion (quintic
hops re-planned from the drawn state, stem twists, accidental swaps)."""
import math

import numpy as np
import pytest

from stream_service.score import glyphs as GL
from stream_service.plugins.sources import score_v2 as SV


def test_glyph_table_and_centres():
    table, AH, AW, outlines = GL.layout()
    assert table.shape == (len(GL.GLYPHS), 12) and AW == 1024 and 0 < AH < 1024
    assert len(outlines) == len(GL.GLYPHS)
    # rects inside the atlas, not overlapping
    rects = [(int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in table]
    for x, y, w, h in rects:
        assert 0 <= x and x + w <= AW and 0 <= y and y + h <= AH
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
    c = GL.centres()
    assert c[GL.BLACK, 0] == pytest.approx(314 / 250 / 2, abs=1e-6)    # Leipzig's black head: 314 units wide
    assert abs(c[GL.BLACK, 1]) < 1e-6                                   # its origin is on its centre line
    assert c[GL.FLAT, 1] < -0.4                                         # a flat's box sits above its origin
    assert table[GL.BLACK, 10] == pytest.approx(314 / 250, abs=1e-6)    # advance
    assert GL.GI["noteheadHalf"] == GL.HALF and GL.CODE_GI[0xE0A4] == GL.BLACK


def _rec(x, y, sp=24.0, glyph=GL.BLACK, stem=None, acc=None, ledgers=0):
    q = np.zeros(GL.NF, np.float32)
    q[0], q[1], q[2], q[3] = x, y, sp, sp
    q[4] = q[5] = glyph
    q[7] = 1.0
    q[12] = -1
    if stem is not None:
        q[8], q[9], q[10], q[11] = x + 1.2 * sp, y, y - 3.5 * sp, 0.05 * sp
    if acc is not None:
        q[12], q[13], q[14], q[29] = acc, x - 1.0 * sp, 1.0, y
    if ledgers:
        q[15], q[16], q[17], q[27], q[28] = y + sp, sp, ledgers, x - 6, x + 36
    return q


def test_boxes_sort_orders_and_bounds():
    sp = 24.0
    R = np.zeros((GL.MAXN, GL.NF), np.float32)
    recs = [_rec(500, 300, stem=True), _rec(100, 200, acc=GL.SHARP), _rec(300, 600, ledgers=2)]
    gate = np.zeros(GL.NF, np.float32)
    gate[4] = gate[5] = gate[12] = -1
    gate[7], gate[8], gate[9], gate[10], gate[11] = 1.0, 50, 100, 400, 2.0
    for k, q in enumerate(recs + [gate]):
        R[k] = q
    N = np.zeros_like(R)
    U = np.zeros(GL.NU, np.float32)
    adv = GL.layout()[0][:, 10].astype(np.float64)
    n = GL.boxes_sort(R, 4, sp, 0.4, N, U, adv)
    assert n == 4 and U[0] == 4
    assert list(N[:4, 23]) == sorted(N[:4, 23])                         # x-sorted by box left
    for q in N[:4]:                                                     # every box holds its own parts
        x0, y0, x1, y1 = q[23:27]
        assert x0 < x1 and y0 < y1
        if q[4] >= 0:
            assert x0 <= q[0] and x1 >= q[0] + sp and y0 <= q[1] - sp and y1 >= q[1] + sp
        if q[11] > 0:
            assert x0 <= q[8] and x1 >= q[8] and y0 <= min(q[9], q[10]) and y1 >= max(q[9], q[10])
        if q[17] > 0:
            assert y1 >= q[15] + (q[17] - 1) * q[16]
        if q[12] >= 0:
            assert x0 <= q[13] - 0.5 * sp
    assert U[2] == N[:4, 23].min() and U[4] == N[:4, 25].max()
    assert GL.boxes_sort(R, 0, sp, 0.4, N, U, adv) == 0 and U[0] == 0


class _Ln:
    def __init__(self, ci):
        self.ci = ci


def test_hop_is_continuous_through_a_replan():
    N = SV._Notes(8)
    i = N.alloc(7)
    ln = _Ln(0)
    offx, offy = np.zeros(2), np.zeros(2)
    N.place(i, ln, 0.0, 0.0)
    N.hop(i, ln, 100.0, 0.0, 0.5, 10.0, 0.0, offx, offy)
    x0, vx0, _a, y0, vy0, _b = N.state_at(i, 0.2)
    N.hop(i, ln, 40.0, 20.0, 0.4, 0.0, 0.2, offx, offy)                 # re-planned mid-flight
    x1, vx1, _a, y1, vy1, _b = N.state_at(i, 0.2)
    assert x1 == pytest.approx(x0, abs=1e-6) and vx1 == pytest.approx(vx0, abs=1e-6)
    assert y1 == pytest.approx(y0, abs=1e-6) and vy1 == pytest.approx(vy0, abs=1e-6)
    xe, vxe, _a, ye, vye, _b = N.state_at(i, 0.61)
    assert (xe, ye, vxe, vye) == (40.0, 20.0, 0.0, 0.0)                 # lands exactly, at rest
    xn, vxn, _a, yn, vyn, _b = N.state_at(i, 0.6 - 1e-9)
    assert xn == pytest.approx(40.0, abs=1e-4) and abs(vxn) < 1e-3       # ... and gets there smoothly
    # to another line: the drawn place carries over in screen space
    offx[1], offy[1] = 300.0, 500.0
    N.hop(i, _Ln(1), 0.0, 0.0, 0.3, 0.0, 0.61, offx, offy)
    xs, _v, _a, ys, _w, _b = N.state_at(i, 0.61)
    assert xs + offx[1] == pytest.approx(40.0) and ys + offy[1] == pytest.approx(20.0)


def test_stem_flip_passes_through_the_head_and_accidental_swaps():
    N = SV._Notes(4)
    i = N.alloc(1)
    N.set_stem(i, True, 1.2, -0.16, -3.5, 0.0, instant=True)
    N.set_stem(i, True, 0.05, 0.16, 3.5, 1.0)                           # up -> down: a twist
    assert N.s_flip[i] and N.s_T[i] == pytest.approx(SV.TWIST_S)
    mid = N.stem_now(i, 1.0 + 0.5 * SV.TWIST_S)
    assert abs(mid[3] - mid[2]) < 0.05                                 # through zero length
    end = N.stem_now(i, 1.0 + SV.TWIST_S)
    assert end[1] == pytest.approx(0.05) and end[3] == pytest.approx(3.5)
    N.set_acc(i, GL.SHARP, -1.0, 0.0, instant=True)
    N.set_acc(i, GL.NATURAL, -1.0, 2.0)
    assert N.aa[i] == GL.SHARP and N.ab[i] == GL.NATURAL and N.a_t0[i] == 2.0
    N.set_glyph(i, GL.HALF, 3.0)
    assert N.ga[i] == GL.BLACK and N.gb[i] == GL.HALF


def test_alloc_free_reuse():
    N = SV._Notes(2)
    a, b = N.alloc(10), N.alloc(11)
    assert N.alloc(12) is None
    N.free(a)
    c = N.alloc(12)
    assert c == a and N.by_id == {11: b, 12: c} and math.isinf(N.t_off[c])
