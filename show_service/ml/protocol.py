"""The ML worker's wire protocol (README S4.3), shared by the worker and anything that feeds or reads it.

stdin carries binary frames: a 48-byte header (``contract.ML_HEADER_FMT``) and a payload, each frame one ``os.write`` of
at most ``ML_PIPE_ATOMIC`` bytes, so even a non-blocking write is all-or-nothing. stdout carries one JSON object per
line (contract's ``Ml*`` TypedDicts, exactly), with no NaN.

Stdlib only at import (numpy only inside the embedding helpers), so ``show_service`` (~/.venvs/sm-show) and the
worker (~/.venvs/sm-show-ml) can both import it. Imports nothing from show_service but ``contract``.
"""
from __future__ import annotations

import base64
import json
import math
import struct
import typing
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from show_service import contract as C

_HDR = struct.Struct(C.ML_HEADER_FMT)
assert _HDR.size == C.ML_HEADER_SIZE == 48
assert C.ML_HEADER_SIZE + 4 * C.ML_FRAME_MAX_SAMPLES <= C.ML_PIPE_ATOMIC
_MAGICS = (C.ML_AUDIO_MAGIC, C.ML_CTRL_MAGIC)
_STREAM_LEN = 16


class ProtocolError(ValueError):
    """A frame that can't be one of ours (bad magic, version or length)."""


@dataclass(frozen=True)
class Frame:
    """One parsed stdin frame. ``pcm`` is float32 bytes (audio) or None; ``ctrl`` the decoded JSON (control)."""
    magic: bytes
    version: int
    flags: int
    n: int
    stream: str
    seg: int
    n0: int
    t0: float
    dropped: int
    payload: bytes
    ctrl: Optional[dict] = None

    @property
    def is_audio(self) -> bool:
        return self.magic == C.ML_AUDIO_MAGIC

    def samples(self):
        """The payload as a float32 numpy array (audio frames only; imports numpy)."""
        import numpy as np
        return np.frombuffer(self.payload, dtype="<f4")


def _stream_bytes(stream: str) -> bytes:
    raw = (stream or "").encode("ascii", "replace")[:_STREAM_LEN]
    return raw + b"\0" * (_STREAM_LEN - len(raw))


def pack_audio(stream: str, seg: int, n0: int, t0: float, dropped: int, flags: int, pcm: bytes | Any) -> bytes:
    """One audio frame. ``pcm`` is little-endian float32 bytes, or anything with ``astype``/``tobytes`` (numpy)."""
    if not isinstance(pcm, (bytes, bytearray, memoryview)):
        pcm = pcm.astype("<f4", copy=False).tobytes()
    n = len(pcm) // 4
    if len(pcm) % 4 or not 1 <= n <= C.ML_FRAME_MAX_SAMPLES:
        raise ProtocolError(f"audio payload of {len(pcm)} bytes: 1..{C.ML_FRAME_MAX_SAMPLES} float32 samples")
    hdr = _HDR.pack(C.ML_AUDIO_MAGIC, C.ML_PROTOCOL_V, flags & 0xFF, n, _stream_bytes(stream), seg & 0xFFFFFFFF,
                    int(n0), float(t0), dropped & 0xFFFFFFFF)
    return hdr + bytes(pcm)


def pack_audio_into(buf: bytearray, stream_b: bytes, seg: int, n0: int, t0: float, dropped: int, flags: int,
                    n: int) -> None:
    """Write the header of an audio frame whose ``n`` samples already sit at ``buf[48:48 + 4n]`` (no allocation)."""
    _HDR.pack_into(buf, 0, C.ML_AUDIO_MAGIC, C.ML_PROTOCOL_V, flags & 0xFF, n, stream_b, seg & 0xFFFFFFFF, int(n0),
                   float(t0), dropped & 0xFFFFFFFF)


def pack_ctrl(obj: dict) -> bytes:
    """One control frame: ``obj`` as compact UTF-8 JSON (at most ML_CTRL_MAX_BYTES)."""
    payload = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if not 1 <= len(payload) <= C.ML_CTRL_MAX_BYTES:
        raise ProtocolError(f"control payload of {len(payload)} bytes: 1..{C.ML_CTRL_MAX_BYTES}")
    return _HDR.pack(C.ML_CTRL_MAGIC, C.ML_PROTOCOL_V, 0, len(payload), b"\0" * _STREAM_LEN, 0, 0, 0.0, 0) + payload


def config_frame(ml: dict) -> bytes:
    return pack_ctrl({"cmd": C.ML_CMD_CONFIG, "v": C.ML_PROTOCOL_V, "ml": ml})


def stop_frame() -> bytes:
    return pack_ctrl({"cmd": C.ML_CMD_STOP})


def parse_header(hdr: bytes) -> tuple:
    """(magic, version, flags, n, stream, seg, n0, t0, dropped, payload_bytes); ProtocolError when it isn't ours."""
    magic, version, flags, n, stream_b, seg, n0, t0, dropped = _HDR.unpack(hdr)
    if magic not in _MAGICS:
        raise ProtocolError(f"bad magic {magic!r}")
    if version != C.ML_PROTOCOL_V:
        raise ProtocolError(f"protocol version {version}, expected {C.ML_PROTOCOL_V}")
    if magic == C.ML_AUDIO_MAGIC:
        if not 1 <= n <= C.ML_FRAME_MAX_SAMPLES:
            raise ProtocolError(f"audio frame of {n} samples")
        size = 4 * n
    else:
        if not 1 <= n <= C.ML_CTRL_MAX_BYTES:
            raise ProtocolError(f"control frame of {n} bytes")
        size = n
    stream = stream_b.split(b"\0", 1)[0].decode("ascii", "replace")
    return magic, version, flags, n, stream, seg, n0, t0, dropped, size


class FrameParser:
    """Bytes in (any chunking) -> whole Frames out. A header that isn't ours is skipped up to the next magic and
    counted (``bad``); a control payload that isn't a JSON object is counted too and yields nothing."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.bad = 0
        self.frames = 0
        self.bytes = 0

    def feed(self, data: bytes) -> Iterator[Frame]:
        self._buf += data
        self.bytes += len(data)
        buf = self._buf
        pos = 0
        try:
            while len(buf) - pos >= C.ML_HEADER_SIZE:
                try:
                    magic, version, flags, n, stream, seg, n0, t0, dropped, size = parse_header(
                        bytes(buf[pos:pos + C.ML_HEADER_SIZE]))
                except ProtocolError:
                    self.bad += 1
                    pos = self._resync(buf, pos + 1)
                    continue
                end = pos + C.ML_HEADER_SIZE + size
                if len(buf) < end:
                    break
                payload = bytes(buf[pos + C.ML_HEADER_SIZE:end])
                pos = end
                ctrl = None
                if magic == C.ML_CTRL_MAGIC:
                    try:
                        ctrl = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        ctrl = None
                    if not isinstance(ctrl, dict):
                        self.bad += 1
                        continue
                self.frames += 1
                yield Frame(magic, version, flags, n, stream, seg, n0, t0, dropped, payload, ctrl)
        finally:
            del buf[:pos]

    @staticmethod
    def _resync(buf: bytearray, start: int) -> int:
        hits = [i for i in (buf.find(m, start) for m in _MAGICS) if i >= 0]
        return min(hits) if hits else max(start, len(buf) - 3)


# -- stdout lines ------------------------------------------------------------------------------------------------------
_SHAPES: dict[str, type] = {C.ML_HELLO: C.MlHello, C.ML_CED: C.MlCed, C.ML_TAGS: C.MlTags, C.ML_KEY: C.MlKey,
                            C.ML_BEATS: C.MlBeats, C.ML_STATS: C.MlStats, C.ML_ERROR: C.MlError}


def shape_keys(kind: str) -> tuple[frozenset, frozenset]:
    """(required, optional) keys of the TypedDict for a line ``type``."""
    td = _SHAPES[kind]
    hints = typing.get_type_hints(td, include_extras=True)
    optional = frozenset(k for k, h in hints.items() if typing.get_origin(h) is typing.NotRequired)
    return frozenset(hints) - optional, optional


_KEYS = {k: shape_keys(k) for k in _SHAPES}


def check_line(obj: Any) -> Optional[str]:
    """None when ``obj`` is exactly one of the worker's line shapes, else why not."""
    if not isinstance(obj, dict):
        return "not an object"
    kind = obj.get("type")
    if kind not in _KEYS:
        return f"unknown type {kind!r}"
    req, opt = _KEYS[kind]
    keys = set(obj)
    if req - keys:
        return f"{kind}: missing {sorted(req - keys)}"
    if keys - req - opt:
        return f"{kind}: unexpected {sorted(keys - req - opt)}"
    return None


def clean(obj: Any, digits: int = 6) -> Any:
    """JSON-safe copy: NaN/inf -> None, floats rounded, tuples -> lists, numpy scalars -> Python."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return round(obj, digits) if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v, digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v, digits) for v in obj]
    item = getattr(obj, "item", None)            # numpy scalar
    if callable(item):
        return clean(item(), digits)
    return obj


def encode_line(obj: dict) -> bytes:
    """One stdout line: compact JSON + newline, no NaN, at most ML_LINE_MAX bytes."""
    data = json.dumps(clean(obj), separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(data) > C.ML_LINE_MAX:
        raise ProtocolError(f"line of {len(data)} bytes > ML_LINE_MAX")
    return data


# -- the embedding -----------------------------------------------------------------------------------------------------
def encode_emb(vec) -> str:
    """float vector -> base64 of little-endian float16 (MlTags.emb)."""
    import numpy as np
    return base64.b64encode(np.asarray(vec, dtype="<f2").tobytes()).decode("ascii")


def decode_emb(b64: str, dim: int):
    """MlTags.emb -> float32 ndarray (dim,). ValueError on a wrong length."""
    import numpy as np
    raw = base64.b64decode(b64)
    if len(raw) != 2 * dim:
        raise ValueError(f"embedding of {len(raw)} bytes, expected {2 * dim}")
    return np.frombuffer(raw, dtype="<f2").astype(np.float32)
