"""Score ribbon: the drawing pass's GPU side (the atlas, the page, the uniforms, the launch).

Source: Original.  Author: smfx.  License: MIT (this file).  license_class: permissive

Per frame: the uniforms go up from this frame's half of a pinned buffer (one async copy), the stage evaluates and
bins its marks on the GPU (ribbon_stage.Stage.frame), and the drawing pass runs straight into the output frame
(render_scale 1) or into the scene's buffer.
"""
from __future__ import annotations

import numpy as np

try:
    from . import ribbon_glyphs as RG, ribbon_kernel as RK, ribbon_stage as RS
except ImportError:
    import ribbon_glyphs as RG
    import ribbon_kernel as RK
    import ribbon_stage as RS


class Painter:
    def __init__(self, ctx, w: int, h: int, night: bool, seed: int, raw_module, launch_2d, helpers: str) -> None:
        cp = ctx.cp
        self.cp, self.ctx, self.w, self.h = cp, ctx, w, h
        self.launch_2d = launch_2d
        mod = raw_module(helpers + RK.SRC)
        self.k = mod.get_function("ribbon")
        self.atlas, table = RG.bake(cp, raw_module)
        self.AW = np.int32(self.atlas.shape[1])
        gt = mod.get_global("c_GT")
        cp.ndarray((table.size,), cp.float32, gt).set(np.ascontiguousarray(table.ravel(), np.float32))
        self.table = table
        self.page = cp.zeros((h, w), cp.uint8)
        launch_2d(mod.get_function("ribbon_bake_page"), w, h,
                  (self.page, np.int32(w), np.int32(h), np.int32(1 if night else 0), np.uint32(seed & 0x7FFFFFFF)))
        self.NC = (w + RK.BIN_W - 1) // RK.BIN_W
        self.P = cp.zeros(RK.NP, cp.float32)
        self._pin = [cp.cuda.alloc_pinned_memory(4 * RK.NP) for _ in range(2)]
        self.host = [np.frombuffer(m, np.float32, RK.NP) for m in self._pin]
        self.ev = [None, None]
        self.k_half = 0
        self.last = {"rec": 0}
        self.graphs: dict = {}
        self.mode = "graph"
        self._cap = None

    def draw(self, out, Ph: np.ndarray, stage, S_px: float, now_s: float) -> None:
        cp = self.cp
        k = self.k_half = 1 - self.k_half
        ev = self.ev[k]
        if ev is not None and not ev.done:
            ev.synchronize()                                   # the GPU is two frames behind: this half is queued
        host = self.host[k]
        host[:] = Ph
        st = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.P.data.ptr, host.ctypes.data, 4 * RK.NP, rt.memcpyHostToDevice, st.ptr)
        if ev is None:
            ev = self.ev[k] = cp.cuda.Event(block=False, disable_timing=True)
        ev.record(st)
        stage.upload(S_px, now_s)
        self.last = {"rec": sum(stage.counts.values())}
        g = self._graph(out, stage) if self.mode == "graph" else None
        if g is not None:
            g.launch(st)
        else:
            self._launch(out, stage)

    def _launch(self, out, stage) -> None:
        stage.launches(self.P)
        self.launch_2d(self.k, self.w, self.h, (out, self.P, stage.CNT, stage.LST, stage.REC, self.atlas, self.AW,
                                                self.page, np.int32(RS.BIN_CAP)))

    def _graph(self, out, stage):
        """The frame's launches captured once per output buffer (degrade: direct launches if capture fails)."""
        key = out.data.ptr
        g = self.graphs.get(key)
        if g is not None:
            return g
        cp = self.cp
        try:
            if self._cap is None:
                self._cap = cp.cuda.Stream(non_blocking=True)
            s = self._cap
            cp.cuda.get_current_stream().synchronize()
            with s:
                s.begin_capture()
                try:
                    self._launch(out, stage)
                except BaseException:
                    try:
                        s.end_capture()
                    except Exception:
                        pass
                    raise
                g = s.end_capture()
        except Exception as exc:
            import logging
            logging.getLogger("stream_service.plugins.score_ribbon").warning(
                "score_ribbon: CUDA graph capture failed (%s); launching directly", exc)
            self.mode = "direct"
            return None
        if len(self.graphs) >= 4:
            self.graphs.pop(next(iter(self.graphs)))
        self.graphs[key] = g
        return g

    def close(self) -> None:
        self.graphs = {}
        self.atlas = self.page = self.P = None
        self.host = self._pin = self.ev = None
