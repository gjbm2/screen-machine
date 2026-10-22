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
        self.ring = RS.PinRing(cp, RK.NP, 4)                     # the uniforms' staging (never waited on)
        self.last = {"rec": 0}
        self.graphs: dict = {}
        self.mode = "graph"
        self._cap = None

    def draw(self, out, Ph: np.ndarray, stage, S_px: float, now_s: float) -> None:
        """Uniforms and changed tables up (pinned staging the GPU has finished with: the render thread never waits
        on the GPU), the stage's evaluation and binning (one CUDA graph a stage, whatever the output buffer), then the
        drawing pass straight into ``out`` (one launch: a worker's output buffers rotate)."""
        cp = self.cp
        b = self.ring.take()
        host = b[1]
        host[:] = Ph
        st = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.P.data.ptr, host.ctypes.data, 4 * RK.NP, rt.memcpyHostToDevice, st.ptr)
        self.ring.sent(b, st)
        stage.upload(S_px, now_s)
        self.last = {"rec": sum(stage.counts.values())}
        g = self._graph(out, stage) if self.mode == "graph" else None
        if g is not None:
            g.launch(st)
        else:
            stage.launches(self.P)
        self._draw(out, stage)

    def _draw(self, out, stage) -> None:
        self.launch_2d(self.k, self.w, self.h, (out, self.P, stage.CNT, stage.LST, stage.REC, self.atlas, self.AW,
                                                self.page, np.int32(RS.BIN_CAP)))

    def _launch(self, out, stage) -> None:
        stage.launches(self.P)

    def _graph(self, out, stage):
        """The stage's launches captured once per stage (degrade: direct launches if capture fails)."""
        key = id(stage)
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
        self.ring = None
