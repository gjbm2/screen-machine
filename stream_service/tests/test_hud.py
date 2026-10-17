"""The live diagnostics panel (hud.py; docs/LIVE_DIAGNOSTICS.md): the text model and codes, the atlas and layout,
the API (create / PATCH / GET / summary), suppression and the draw's gates and self-timing (fake engine; the
kernel itself runs in the GPU tests)."""
from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

from stream_service import hud as H

from .test_cue import AUTO, live
from .test_sessions import AUTH, client  # noqa: F401 (fixture)


def test_encode_codes_and_tones():
    g, t = H.encode(["ab · c→d", "x" * 80], [1])
    grid = np.frombuffer(g, np.uint8).reshape(H.ROWS, H.COLS)
    assert bytes((grid[0, :8] + H.FIRST).tolist()).decode() == "ab . c>d"
    assert grid[1].max() == ord("x") - H.FIRST and len(t) == H.ROWS and t[1] == 1 and t[0] == 0
    assert grid.max() < H.NGLYPH


def test_atlas_and_layout():
    atlas, cw, ch = H.bake_atlas(36)
    assert atlas.shape == (H.NGLYPH, ch, cw) and ch == 36 and atlas[ord("M") - H.FIRST].max() > 200
    assert atlas[0].max() == 0                                       # a space is empty
    x0, y0, pw, ph, pad = H.layout(3840, 2160, cw, ch)
    assert (x0, y0) == (64, 64) and pw % 32 == 0 and ph % 32 == 0 and pw * ph < 0.12 * 3840 * 2160
    assert H.layout(1080, 1920, cw, ch)[2] <= 1080 - 128              # portrait: clamped


class _Ev:
    def __init__(self):
        self.done = True

    def record(self, *_):
        pass


def test_draw_gates_uploads_and_self_timing():
    calls = []
    d = H.HudDraw.__new__(H.HudDraw)
    d.cp = SimpleNamespace(cuda=SimpleNamespace(runtime=SimpleNamespace(memcpyAsync=lambda *a: calls.append("up"),
                                                                         memcpyHostToDevice=1),
                                                get_elapsed_time=lambda a, b: 0.1))
    d.kernel = lambda *a: calls.append("k")
    d.w, d.h, d.fps, d.cw, d.ch = 64, 32, 30, 4, 8
    d.x0, d.y0, d.pw, d.ph, d.pad = 0, 0, 32, 16, 2
    d.d_atlas, d.n = None, H.ROWS * H.COLS + H.ROWS
    d.host = [np.zeros(d.n, np.uint8), np.zeros(d.n, np.uint8)]
    d.up_ev, d.up_live = [_Ev(), _Ev()], [False, False]
    d.t0, d.t1, d.timing = _Ev(), _Ev(), False
    d.on, d.pending, d.active, d.ready, d.fade = True, None, 0, False, 0.0
    d.failed, d.ema_ms, d.slow_since, d.lamp = None, None, None, None
    # numpy arrays stand in for device buffers: give them a .data.ptr
    class Buf(np.ndarray):
        @property
        def data(self):
            return SimpleNamespace(ptr=0)
    d.d_buf = [np.zeros(d.n, np.uint8).view(Buf), np.zeros(d.n, np.uint8).view(Buf)]
    nv12 = np.zeros(64 * 32 * 3 // 2, np.uint8)
    stream = SimpleNamespace(ptr=0)
    d.draw(nv12, 1.0, 1, stream)
    assert calls == []                                               # nothing uploaded yet: no draw
    d.set(*H.encode(["hello"], []))
    d.draw(nv12, 1.0, 2, stream)
    assert calls == ["up", "k"] and d.ready
    calls.clear()
    d.draw(nv12, 0.0, 3, stream)                                     # the bus is closed: no panel on the frame
    assert calls == []
    d.cp.cuda.get_elapsed_time = lambda a, b: 9.0                    # one frame over HARD_MS: off
    d.timing = True
    d.draw(nv12, 1.0, 4, stream)
    assert d.failed == "too slow"


async def test_api_create_patch_get_and_suppression(client):
    sid, s, eng = await live(client, auto=AUTO)
    # the fake engine has no panel: the API still answers, lines built on request
    r = await client.patch(f"/api/sessions/{sid}", json={"hud": True}, headers=AUTH)
    assert r.status == 200 and s.hud_wanted
    r = await client.get(f"/api/sessions/{sid}/hud", headers=AUTH)
    body = await r.json()
    assert r.status == 200 and set(body) == {"on", "failed", "at", "lines", "warn"} and body["lines"][0].startswith("devtest")
    assert len(body["lines"]) <= H.ROWS and all(len(H._ascii(x)) for x in body["lines"])
    assert (await client.patch(f"/api/sessions/{sid}", json={"hud": "yes"}, headers=AUTH)).status == 400
    summ = (await (await client.get(f"/api/sessions/{sid}", headers=AUTH)).json())["hud"]
    assert set(summ) == {"on", "failed", "rate_hz"}
    # a fake panel: the tick drives it, cal_flash turns it off
    s.engine.hud = SimpleNamespace(on=False, fade=0.0, lamp=None, failed=None, ema_ms=None, set=lambda g, t: None)
    s._hud_sync(time.monotonic())
    assert s.engine.hud.on
    s._hud_suppressed = lambda: True
    s._hud_sync(time.monotonic() + 1)
    assert not s.engine.hud.on


def test_long_parts_are_cut_to_fit():
    assert H._cut("drift_speed", 9) == "drift_sp~" and H._cut("ok", 9) == "ok" and H._cut(None, 4) == "-"
