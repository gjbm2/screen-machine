"""GPU-resident NVENC (4K60 plan change 2): PyNvVideoCodec 2.2.3 encodes the render stream's CuPy NV12 surfaces.

No host copy and no ffmpeg: the renderer converts each frame into one of its NV12 slot surfaces on the render
stream, and the encode thread hands that surface to ``Encode()`` (the GIL is released inside it).  The encoder is
created on CuPy's context with the render stream, so its input copy is stream-ordered after the frame's kernels;
the encode thread still waits for the slot's event first (never the render thread).  HEVC P4, ultra-low-latency
tuning, spatial AQ 12, CBR at the profile's bitrate (VBV 2 s, as ffmpeg's default, or the profile's ``vbv_s``), GOP as
the profile, no B-frames, parameter sets repeated on every IDR.  ``submit(..., idr=True)`` forces an IDR with
VPS/SPS/PPS.  A profile's ``level`` is held at Main tier (``level``/``tier`` options): NVENC otherwise re-derives the level
from the frame rate, bitrate and buffer (4K60 at 40M with a 2 s buffer is High tier 5.1).

``set_fps(fps)`` (the degradation controller's fps step, degrade.py): Reconfigure() the frame rate, no IDR, same
bitrate and buffer (1.2 ms on the P4000).  With the level held, the only change in the stream is the SPS's VUI
timing, which bitstream.HevcAnnexB pins back to the record's.

Packets become what today's ffmpeg FLV path delivers (bitstream.HevcAnnexB): an hvcC from GetSequenceParams(), the
same length-prefixed samples, and the profile's colour in the SPS VUI (rewritten; PyNvVideoCodec has no VUI colour
option) as well as in the init segment's colr box.  Byte-identical for the same coded packets
(tests/test_nvenc.py).  PyNvVideoCodec holds its output back by 3 frames (measured: 54 ms at 60p, 100 ms at 30p).
Its packets' ``timestamp`` is its own input count, whatever ``inputTimeStamp`` says (measured, 19 Oct), so each
packet gets back the frame id it was submitted with: no B-frames, so packets come out in input order.

Same interface as encoder.NvencPipeline for the renderer: on_config / on_frame callbacks (here called on the
thread that calls submit()), ``submit(frame, frame_id, idr)`` and ``close()``.  It raises instead of exiting: the
renderer then falls back to the ffmpeg pipeline for the rest of the session.
"""
from __future__ import annotations

import collections
import time
from typing import Any, Callable, Optional

from .bitstream import HevcAnnexB, StreamConfig
from .encoder import _parse_rate

PRESET = "P4"
TUNING = "ultra_low_latency"
AQ = "12"            # spatial AQ strength 12 (profiles.AQ: the ffmpeg path's -spatial-aq 1 -aq-strength 12)
OUTPUT_DELAY = 3     # frames PyNvVideoCodec holds its output back (measured on the P4000, 18 Oct)


class _CAI:
    __slots__ = ("__cuda_array_interface__",)

    def __init__(self, shape: tuple, strides: tuple, ptr: int):
        self.__cuda_array_interface__ = {"shape": shape, "strides": strides, "data": (int(ptr), False),
                                         "typestr": "|u1", "version": 3}


class Surface:
    """An NV12 device buffer (pitch = width) as PyNvVideoCodec takes it: ``cuda()`` -> [luma, interleaved chroma]."""
    __slots__ = ("arr", "_planes")

    def __init__(self, arr: Any, w: int, h: int):
        ptr = arr.data.ptr
        self.arr = arr
        self._planes = [_CAI((h, w, 1), (w, 1, 1), ptr), _CAI((h // 2, w // 2, 2), (w, 2, 1), ptr + w * h)]

    def cuda(self) -> list:
        return self._planes


class GpuEncoder:
    def __init__(self, codec: str, width: int, height: int, fps: int, *, cp: Any, stream: Any, bitrate: str,
                 gop_seconds: float = 1.0, colr: Optional[tuple[int, int, int, bool]] = None, name: str = "",
                 vbv_s: Optional[float] = None, level: Optional[str] = None,
                 clock: Callable[[], float],
                 on_config: Callable[[StreamConfig], None],
                 on_frame: Callable[[int, bool, bytes, float], None]):
        if codec != "hevc":
            raise ValueError(f"the GPU encoder is HEVC only, not {codec}")
        import PyNvVideoCodec as nvc

        self.nvc = nvc
        self.w, self.h, self.fps = width, height, fps
        self.clock, self.on_frame, self.tag = clock, on_frame, name
        self.delay_frames = OUTPUT_DELAY
        bits = _parse_rate(bitrate)
        self.gop = max(1, round(gop_seconds * fps))
        self.opts = {"codec": "hevc", "preset": PRESET, "tuning_info": TUNING, "rc": "cbr", "fps": str(fps),
                     "gop": str(self.gop), "idrperiod": str(self.gop), "bf": "0", "bitrate": str(bits),
                     "maxbitrate": str(bits), "vbvbufsize": str(int((vbv_s or 2.0) * bits)), "repeatspspps": "1",
                     "aq": AQ}
        if level:
            self.opts.update(level=str(level), tier="main")
        self.fps_now = fps
        self.reconfigures = 0
        self._ids: collections.deque[int] = collections.deque()    # frame ids in, in order: packets out map back
        ctx = int(cp.cuda.driver.ctxGetCurrent())
        if not ctx:
            raise RuntimeError("no current CUDA context")
        self._dev = cp.cuda.Device()
        # gpu_id: the context's device (PCI order), else PyNvVideoCodec assumes 0 and refuses a GPU 1 context
        self.enc = nvc.CreateEncoder(width, height, "NV12", False, cudacontext=ctx, cudastream=int(stream.ptr),
                                     gpu_id=str(self._dev.id), **self.opts)
        if self.enc.GetNumBFrames():
            raise RuntimeError(f"encoder has {self.enc.GetNumBFrames()} B-frames")
        self._idr = int(nvc.NV_ENC_PIC_FLAGS.FORCEIDR.value) | int(nvc.NV_ENC_PIC_FLAGS.OUTPUT_SPSPPS.value)
        self.mux = HevcAnnexB(width, height, colr)
        self.config = self.mux.on_param_sets(bytes(self.enc.GetSequenceParams()))
        self.frames_in = self.frames_out = self.idrs_forced = 0
        on_config(self.config)

    def adopt(self, name: str, on_config: Callable[[StreamConfig], None],
              on_frame: Callable[[int, bool, bytes, float], None]) -> None:
        """A session takes this encoder (made ahead by a render worker, worker.py): its callbacks, then its config."""
        self.tag, self.on_frame = name, on_frame
        on_config(self.config)

    def surface(self, arr: Any) -> Surface:
        return Surface(arr, self.w, self.h)

    def submit(self, frame: Surface, frame_id: int = -1, idr: bool = False) -> float:
        """Encode one frame (blocks in Encode(), GIL released); packets out (3 frames late) go to on_frame here.
        Returns the clock() time at which the encoder had taken the frame.  Raises on an encoder error."""
        enc = self.enc
        if enc is None:
            raise RuntimeError("encoder closed")
        pp = self.nvc.NV_ENC_PIC_PARAMS()
        pp.inputTimeStamp = frame_id
        if idr:
            pp.encodePicFlags = self._idr
            self.idrs_forced += 1
        pkts = enc.Encode(frame, pp)
        t = self.clock()
        self.frames_in += 1
        ids = self._ids
        ids.append(frame_id)
        for p in pkts:
            sample, key = self.mux.sample(p["data"])
            self.frames_out += 1
            self.on_frame(ids.popleft() if ids else int(p["timestamp"]), key, sample, t)
        return t

    def set_fps(self, fps: int) -> float:
        """Reconfigure() to this frame rate (the encode thread, between two Encode() calls; no IDR).  -> ms.  Raises
        if the encoder refuses: the caller keeps encoding at the old rate's bits per frame."""
        enc = self.enc
        if enc is None:
            raise RuntimeError("encoder closed")
        if fps == self.fps_now:
            return 0.0
        rp = enc.GetEncodeReconfigureParams()
        rp.frameRateNum, rp.frameRateDen = int(fps), 1
        t0 = time.perf_counter()
        if enc.Reconfigure(rp) is False:
            raise RuntimeError(f"Reconfigure to {fps} fps refused")
        self.fps_now = int(fps)
        self.reconfigures += 1
        return (time.perf_counter() - t0) * 1000.0

    def close(self) -> None:
        enc, self.enc = self.enc, None
        if enc is None:
            return
        try:
            with self._dev:
                enc.EndEncode()   # the frames still held back are dropped: the session is over
        except Exception:
            pass
        del enc
