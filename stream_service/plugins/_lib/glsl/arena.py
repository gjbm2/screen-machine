"""One CUDA graph per frame for the GLSL runtime (4K60 plan change 4, docs/perf/PLAN.md).

Under WSL every CUDA call costs ~45-60 us of host time (p99 in ms) and holds the GIL.  The direct path makes
9-17 of them per GLSL plug-in per frame: three texture ``.set()`` uploads plus the palette's, an elementwise
notes copy, every launch, and two fresh CUDA events (created, recorded, queried, destroyed).  A
:class:`FramePlan` turns a frame into

    e0.record  ->  [host-side node parameter updates]  ->  ONE graph launch  ->  fence.record

(transitions: the graph launch alone).

* **Every launch stays by value** (:meth:`node`): the same kernels with the same argument bytes as the direct
  path, so the same pixels and the same GPU time (an arena-pointer variant of the shader kernel measured up to
  2x slower from register pressure: uniforms leave the constant bank).  In the graph, each frame's values go into
  the instantiated nodes with ``cuGraphExecKernelNodeSetParams`` (driver API via ctypes; host-side only, ~5 us,
  where a launch costs ~45-60 us).
* **The arena** holds what kernels read through a pointer: the synthesised audio textures and the palette
  (:meth:`static` regions).  The plug-in writes them into a numpy staging buffer; :meth:`run` copies it into one
  of ``slots`` pinned buffers and the graph's first node copies that slot into the device arena by reading the
  pinned memory directly (zero-copy).  A kernel, not a cudaMemcpy: under WDDM a copy-engine transfer cost
  0.05-0.09 ms of GPU time per frame in engine syncs (measured); the kernel ~0.01 ms.
* **The graph** is keyed by the frame's *signature* (kernel, grid, block, argument types per launch).  A
  structural change (render scale, an effect's mix branch, an ISF size expression) captures a new one, kept in
  a small LRU; values (uniforms, sampler and output pointers, ping-pong buffers) never need a re-capture.
* **Fences**: a pinned slot is rewritten only after the GPU has read it (the fence recorded after its frame).
  Normally that is already known; if the GPU is ``slots`` frames behind (deeper than the renderer's own
  pipeline), the host waits: backpressure, never a wrong frame.
* **Degrade, never refuse**: if the driver calls, capture or an update fail, the same launches run directly
  (one launch each); the caller falls back to its direct path on any other error.  Each is logged once.

The plan also times the plug-in's GPU work (e0 -> fence, read one frame late, no sync): :meth:`poll_ms` is a
drop-in for ``_Watch.poll``.
"""
from __future__ import annotations

import collections
import ctypes
import logging
import threading
import time
from typing import Any, Optional

import numpy as np

log = logging.getLogger("stream_service.plugins.glsl")

ALIGN = 16
MAX_GRAPHS = 8
THRASH = (8, 120)          # 8 captures within 120 frames: the shape changes every frame; direct launches instead
_logged: set = set()


def log_once(key: Any, msg: str, *args) -> None:
    if key in _logged:
        return
    _logged.add(key)
    log.warning(msg, *args)


def _up(n: int, a: int = ALIGN) -> int:
    return (n + a - 1) // a * a


# ------------------------------------------------------------------------------------------- driver API (ctypes)

class _KNP(ctypes.Structure):
    """CUDA_KERNEL_NODE_PARAMS_v2 (cuda.h, CUDA 12)."""
    _fields_ = [("func", ctypes.c_void_p), ("gridDimX", ctypes.c_uint), ("gridDimY", ctypes.c_uint),
                ("gridDimZ", ctypes.c_uint), ("blockDimX", ctypes.c_uint), ("blockDimY", ctypes.c_uint),
                ("blockDimZ", ctypes.c_uint), ("sharedMemBytes", ctypes.c_uint),
                ("kernelParams", ctypes.POINTER(ctypes.c_void_p)), ("extra", ctypes.POINTER(ctypes.c_void_p)),
                ("kern", ctypes.c_void_p), ("ctx", ctypes.c_void_p)]


class _Driver:
    """The driver calls the graph path needs; ``ok`` False if libcuda lacks them (then: direct launches)."""

    def __init__(self):
        self.ok = False
        try:
            lib = ctypes.CDLL("libcuda.so.1")
            self.capinfo = lib.cuStreamGetCaptureInfo_v2
            self.capinfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                     ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)), ctypes.POINTER(ctypes.c_size_t)]
            self.capinfo.restype = ctypes.c_int
            self.getparams = lib.cuGraphKernelNodeGetParams_v2
            self.getparams.argtypes = [ctypes.c_void_p, ctypes.POINTER(_KNP)]
            self.getparams.restype = ctypes.c_int
            self.setparams = lib.cuGraphExecKernelNodeSetParams_v2
            self.setparams.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_KNP)]
            self.setparams.restype = ctypes.c_int
            self.hostptr = lib.cuMemHostGetDevicePointer_v2
            self.hostptr.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), ctypes.c_void_p, ctypes.c_uint]
            self.hostptr.restype = ctypes.c_int
            self.ok = True
        except Exception as e:
            log_once("driver", "GLSL fast path: libcuda graph-node calls unavailable (%s); direct launches", e)

    def last_node(self, stream_ptr: int) -> int:
        """The node the capture on ``stream_ptr`` added last."""
        st, cid, g = ctypes.c_int(), ctypes.c_ulonglong(), ctypes.c_void_p()
        deps, nd = ctypes.POINTER(ctypes.c_void_p)(), ctypes.c_size_t()
        r = self.capinfo(ctypes.c_void_p(stream_ptr), ctypes.byref(st), ctypes.byref(cid), ctypes.byref(g),
                         ctypes.byref(deps), ctypes.byref(nd))
        if r != 0 or st.value != 1 or nd.value != 1:
            raise RuntimeError(f"cuStreamGetCaptureInfo: error {r}, status {st.value}, {nd.value} dependencies")
        return int(deps[0])

    def device_ptr(self, host_ptr: int) -> int:
        """The device address of pinned host memory (zero-copy)."""
        d = ctypes.c_ulonglong()
        r = self.hostptr(ctypes.byref(d), ctypes.c_void_p(host_ptr), 0)
        if r != 0:
            raise RuntimeError(f"cuMemHostGetDevicePointer error {r}")
        return int(d.value)


_driver: Optional[_Driver] = None
_driver_lock = threading.Lock()


def driver() -> _Driver:
    global _driver
    with _driver_lock:
        if _driver is None:
            _driver = _Driver()
        return _driver


# ------------------------------------------------------------------------------------------- small kernels

KERNELS_SRC = r"""
extern "C" __global__ void arena_copy(const ulonglong2* __restrict__ src, ulonglong2* __restrict__ dst, const int n16)
{
    for (int i = blockIdx.x * blockDim.x + threadIdx.x; i < n16; i += gridDim.x * blockDim.x) dst[i] = src[i];
}

// ShowTextures' notes from the GPU (cupy.minimum(notes_gpu, cap) in the direct path); src null: host notes,
// already in the arena.
extern "C" __global__ void glsl_notes_min(const float* __restrict__ src, float* __restrict__ dst, const int n,
                                          const float cap)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (src == nullptr || i >= n) return;
    dst[i] = fminf(src[i], cap);
}
"""


def small_kernels():
    """(arena_copy, glsl_notes_min), compiled once per process."""
    from stream_service.gpu import raw_module
    mod = raw_module(KERNELS_SRC)
    return mod.get_function("arena_copy"), mod.get_function("glsl_notes_min")


def grid_2d(w: int, h: int) -> tuple:
    """launch_2d's grid and block (32 x 8)."""
    return ((w + 31) // 32, (h + 7) // 8, 1), (32, 8, 1)


def grid_1d(n: int) -> tuple:
    return ((n + 255) // 256, 1, 1), (256, 1, 1)


def copy_frame(cp, dst, src) -> None:
    """dst[...] = src: one async device copy when both are contiguous and alike (cupy.copyto otherwise)."""
    if dst.shape == src.shape and dst.dtype == src.dtype and dst.flags.c_contiguous and src.flags.c_contiguous:
        s = cp.cuda.get_current_stream()
        cp.cuda.runtime.memcpyAsync(int(dst.data.ptr), int(src.data.ptr), int(dst.nbytes),
                                    cp.cuda.runtime.memcpyDeviceToDevice, s.ptr)
    else:
        cp.copyto(dst, src)


# ------------------------------------------------------------------------------------------- the plan

# Retired plans stay referenced until their last frame is done on the GPU, so the arena (which the notes kernel
# writes) is never handed to another allocation while queued work still touches it.
_graveyard: list = []
_grave_lock = threading.Lock()


def _prune() -> None:
    with _grave_lock:
        keep = []
        for fence, plan in _graveyard:
            try:
                done = fence.done
            except Exception:
                done = True
            if not done:
                keep.append((fence, plan))
        _graveyard[:] = keep


class _Captured:
    """One instantiated graph and its kernel nodes in launch order: (handle, _KNP, keep-alive)."""

    def __init__(self, graph, nodes: list):
        self.graph = graph
        self.exec = ctypes.c_void_p(int(graph.graphExec))
        self.nodes = nodes


class FramePlan:
    """One graph per frame.  Per frame: :meth:`begin`, :meth:`node` once per launch (in order), :meth:`run`.

    ``static_bytes`` reserves the arena (textures, palette): :meth:`static` hands out offsets in it at setup and
    :meth:`view` gives numpy views of the staging buffer to write each frame.  ``arg_bytes`` bounds one frame's
    by-value kernel arguments."""

    def __init__(self, cp, static_bytes: int, *, arg_bytes: int = 4096, slots: int = 8, name: str = "",
                 graphs: bool = True):
        self.cp, self.name = cp, name
        self.cap = _up(static_bytes)
        self.static_end = 0
        self.stage = np.zeros(max(self.cap, 16), np.uint8)
        self.args = np.zeros(_up(arg_bytes), np.uint8)      # by-value kernel arguments (host only; never moves)
        self.args_ptr = int(self.args.ctypes.data)
        self.slots = slots
        self._e0 = [cp.cuda.Event() for _ in range(slots)]
        self._fence = [cp.cuda.Event() for _ in range(slots)]
        self._fence_seq = [-1] * slots
        self.seq = 0                    # frames run
        self.done_seq = -1              # every frame <= this is known complete on the GPU
        self.pending: Optional[tuple] = None
        self._last_stream = None
        self._acursor = 0
        self._launches: list = []
        self._sig: list = []
        self._views: dict = {}
        self._layouts: dict = {}
        self._graphs: "collections.OrderedDict[tuple, _Captured]" = collections.OrderedDict()
        self._cap_stream = None
        drv = driver()
        self.graphs_ok = graphs and drv.ok
        # the arena: pinned slots, the device copy, and the copy kernel's arguments (src slot, dst, n16)
        self.dev = cp.zeros(max(self.cap, 16), cp.uint8)
        self.dev_ptr = int(self.dev.data.ptr)
        self._copy_args = np.zeros(3, np.uint64)
        self._copy_args[1] = self.dev_ptr
        self._copy_args.view(np.int32)[4] = self.cap // 16
        self._copy_fn, _ = small_kernels()
        self._pinned, self._host, self._host_ptr, self._host_dev = [], [], [], None
        if self.cap:
            self._pinned = [cp.cuda.alloc_pinned_memory(self.cap) for _ in range(slots)]
            self._host = [np.frombuffer(m, np.uint8, self.cap) for m in self._pinned]
            self._host_ptr = [int(m.ptr) for m in self._pinned]
            if drv.ok:
                try:
                    self._host_dev = [drv.device_ptr(p) for p in self._host_ptr]
                except Exception as e:
                    log_once(("zerocopy", name), "%s: zero-copy arena upload unavailable (%s); cudaMemcpy instead",
                             name, e)
        # counters (tests and the bench read them)
        self.n_captures = 0
        self.n_graph_runs = 0
        self.n_direct_runs = 0
        self.n_stalls = 0
        self.n_queries = 0
        self.n_setparams = 0
        self.capture_ms = 0.0
        self._cap_seqs: collections.deque = collections.deque(maxlen=THRASH[0])
        if _graveyard:
            _prune()

    def retire(self) -> None:
        """The plug-in is closing: keep this plan alive until its last frame has run on the GPU."""
        if self.seq > 0:
            with _grave_lock:
                _graveyard.append((self._fence[(self.seq - 1) % self.slots], self))
        _prune()

    # -- layout ---------------------------------------------------------------------------------------------
    def static(self, nbytes: int) -> int:
        """An aligned offset in the arena (setup time)."""
        off = self.static_end
        end = off + _up(nbytes)
        if end > self.cap:
            raise ValueError(f"arena full ({end} > {self.cap})")
        self.static_end = end
        return off

    def view(self, off: int, dtype, shape=(), buf: Optional[np.ndarray] = None) -> np.ndarray:
        """A cached numpy view of the staging buffer (or ``buf``) at ``off``; the buffers never move."""
        b = self.stage if buf is None else buf
        key = (off, dtype, shape, buf is None)
        v = self._views.get(key)
        if v is None:
            dt = np.dtype(dtype)
            n = int(np.prod(shape)) if shape else 1
            v = np.frombuffer(b.data, dt, n, off)
            v = v.reshape(shape) if shape else v.reshape(())
            self._views[key] = v
        return v

    # -- per frame ------------------------------------------------------------------------------------------
    def begin(self) -> None:
        self._acursor = 0
        self._launches = []
        self._sig = []

    def node(self, fn, grid: tuple, block: tuple, args: tuple, types: Optional[tuple] = None) -> None:
        """Add ``fn(grid, block, args)`` exactly as a direct launch would pass it.  ``args`` are numpy scalars
        (a structured np.void for a uniform record); their values are copied now.  ``types`` (their dtypes) may
        be passed by a caller that knows them, to save the lookups."""
        if types is None:
            types = tuple(x.dtype for x in args)
        cur = self._acursor
        lay = self._layouts.get((cur, types))
        if lay is None:
            lay = self._layouts[(cur, types)] = self._layout(cur, types)
        views, offs, end = lay
        for v, x in zip(views, args):
            v[()] = x
        self._acursor = end
        self._launches.append((fn, grid, block, offs, types))
        self._sig.append((id(fn), grid, block, types))

    def _layout(self, cur: int, types: tuple) -> tuple:
        a = self.args
        views, offs = [], []
        for dt in types:
            n = dt.itemsize
            cur = _up(cur, 16 if n >= 16 else (8 if n >= 8 else 4))
            if cur + n > a.size:
                raise ValueError(f"kernel arguments full ({cur + n} > {a.size})")
            views.append(self.view(cur, dt, (), a))
            offs.append(cur)
            cur += n
        return views, tuple(offs), cur

    def _argv(self, offs: tuple, types: tuple) -> tuple:
        a = self.args
        return tuple(self.view(o, t, (), a)[()] for o, t in zip(offs, types))

    def _copy_argv(self) -> tuple:
        c = self._copy_args
        return (np.uint64(c[0]), np.uint64(c[1]), np.int32(self.cap // 16))

    def _copy_launch(self) -> tuple:
        n16 = self.cap // 16
        return ((min(64, (n16 + 255) // 256), 1, 1), (256, 1, 1))

    def run(self, stream=None, timed: bool = True):
        """Upload this frame's arena and launch its graph on ``stream`` (default: the current stream).
        ``timed``: record e0 for :meth:`poll_ms`.  Returns the fence (None for an untimed plan without arena)."""
        cp = self.cp
        if stream is None:
            stream = cp.cuda.get_current_stream()
        k = self.seq % self.slots
        upload = self.cap > 0
        fenced = upload or timed
        if upload:
            prev = self._fence_seq[k]
            if prev > self.done_seq:                     # this slot's last upload may still be queued
                self.n_queries += 1
                if self._fence[k].done:
                    self.done_seq = prev
                else:
                    self.n_stalls += 1
                    self._fence[k].synchronize()         # GPU `slots` frames behind: wait (backpressure)
                    self.done_seq = prev
            self._host[k][:] = self.stage[:self.cap]
            if self._host_dev is not None:
                self._copy_args[0] = self._host_dev[k]
        sptr = stream.ptr
        if fenced and self._last_stream is not None and self._last_stream != sptr and self.seq > 0:
            # another stream than last frame: order after our previous frame (shared arena and buffers)
            stream.wait_event(self._fence[(self.seq - 1) % self.slots])
        self._last_stream = sptr
        e0 = self._e0[k]
        if timed:
            e0.record(stream)
        copy_node = upload and self._host_dev is not None
        if upload and not copy_node:                     # (no zero-copy address: a copy-engine transfer)
            cp.cuda.runtime.memcpyAsync(self.dev_ptr, self._host_ptr[k], self.cap,
                                        cp.cuda.runtime.memcpyHostToDevice, sptr)
        g = self._graph() if self.graphs_ok else None
        if g is not None and self._set_params(g):
            g.graph.launch(stream)
            self.n_graph_runs += 1
        else:
            with stream:
                if copy_node:
                    self._copy_fn(*self._copy_launch(), self._copy_argv())
                for fn, grid, block, offs, types in self._launches:
                    fn(grid, block, self._argv(offs, types))
            self.n_direct_runs += 1
        if not fenced:
            self.seq += 1
            return None
        fence = self._fence[k]
        fence.record(stream)
        self._fence_seq[k] = self.seq
        if timed:
            self.pending = (e0, fence, self.seq)
        self.seq += 1
        return fence

    def _set_params(self, g: _Captured) -> bool:
        """This frame's argument values into the graph's nodes (the driver reads them now)."""
        setp = _driver.setparams
        ex = g.exec
        for node, knp, _ in g.nodes:
            r = setp(ex, node, ctypes.byref(knp))
            if r != 0:
                self._graphs_off(f"cuGraphExecKernelNodeSetParams error {r}")
                return False
        self.n_setparams += len(g.nodes)
        return True

    def _graphs_off(self, why: str) -> None:
        self.graphs_ok = False
        self._graphs.clear()
        log_once(("graphs", self.name), "%s: CUDA graphs off (%s); direct launches instead", self.name, why)

    def _graph(self) -> Optional[_Captured]:
        sig = tuple(self._sig)
        g = self._graphs.get(sig)
        if g is not None:
            self._graphs.move_to_end(sig)
            return g
        q = self._cap_seqs
        if len(q) == q.maxlen and self.seq - q[0] < THRASH[1]:
            self._graphs_off(f"{q.maxlen} captures in {self.seq - q[0]} frames")
            return None
        q.append(self.seq)
        g = self._capture()
        if g is None:
            return None
        self._graphs[sig] = g
        while len(self._graphs) > MAX_GRAPHS:
            self._graphs.popitem(last=False)
        return g

    def _capture(self) -> Optional[_Captured]:
        cp = self.cp
        drv = driver()
        t0 = time.perf_counter()
        try:
            if self._cap_stream is None:
                self._cap_stream = cp.cuda.Stream(non_blocking=True)
            cs = self._cap_stream
            handles = []
            with cs:
                cs.begin_capture(cp.cuda.runtime.streamCaptureModeThreadLocal)
                try:
                    if self.cap and self._host_dev is not None:
                        self._copy_fn(*self._copy_launch(), self._copy_argv())
                        handles.append((drv.last_node(cs.ptr), None))
                    for fn, grid, block, offs, types in self._launches:
                        fn(grid, block, self._argv(offs, types))
                        handles.append((drv.last_node(cs.ptr), offs))
                finally:
                    graph = cs.end_capture()
            nodes = []
            for node, offs in handles:
                knp = _KNP()
                r = drv.getparams(ctypes.c_void_p(node), ctypes.byref(knp))
                if r != 0:
                    raise RuntimeError(f"cuGraphKernelNodeGetParams error {r}")
                if offs is None:                          # the arena copy: src, dst, n16
                    base = int(self._copy_args.ctypes.data)
                    ptrs = [base, base + 8, base + 16]
                else:
                    ptrs = [self.args_ptr + o for o in offs]
                argv = (ctypes.c_void_p * len(ptrs))(*ptrs)
                knp.kernelParams = ctypes.cast(argv, ctypes.POINTER(ctypes.c_void_p))
                knp.extra = None
                nodes.append((ctypes.c_void_p(node), knp, argv))
            self.n_captures += 1
            self.capture_ms = (time.perf_counter() - t0) * 1e3
            return _Captured(graph, nodes)
        except Exception as e:                            # degrade: direct launches, same arguments
            self._graphs_off(f"capture failed: {type(e).__name__}: {e}")
            return None

    # -- timing ---------------------------------------------------------------------------------------------
    def poll_ms(self) -> Optional[float]:
        """GPU ms of the last frame (e0 -> fence) once it is done, else None; never syncs.  A ``_Watch.poll``."""
        if self.pending is None:
            return None
        e0, e1, seq = self.pending
        if seq > self.done_seq:
            self.n_queries += 1
            if not e1.done:
                return None
            self.done_seq = seq
        self.pending = None
        return self.cp.cuda.get_elapsed_time(e0, e1)
