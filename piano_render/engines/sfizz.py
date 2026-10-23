"""sfizz engine: SFZ libraries through libsfizz's C API (ctypes), in process.

The library: $PIANO_RENDER_SFIZZ_LIB if set, else the first libsfizz.so* under <engines_root>/sfizz/ (the OBS
xUbuntu_24.04 libsfizz1 1.2.3 .deb unpacked there). If it needs a library the system lacks, the missing one is
looked for under the same tree and loaded first by absolute path (RTLD_GLOBAL), so no LD_LIBRARY_PATH is needed.

Settings (research r7): freewheeling on, whole samples preloaded (PRELOAD below: freewheeling alone did not stop
sfizz 1.2.3 from cutting notes short; streaming, realtime = True, instead runs in live mode with STREAM_PRELOAD),
sample quality 2 in both process modes (10 is 5-6x slower for no audible gain), 256 voices (streaming: the
instrument's "stream_voices" when it has one; sfizz can't change it mid-render: it silences every voice), 512-frame blocks,
every event at its exact frame offset inside the block. Note-offs carry their release velocity.

pitch_cents: sfizz 1.2.3 ignores RPN 0 (its bend range is the SFZ's bend_up/bend_down, 200 cents unless the file
says otherwise; measured 22 Oct), so instead of the pitch wheel we move the tuning reference,
sfizz_set_tuning_frequency(440 * 2 ** (cents / 1200)): every region shifts by exactly that many cents (measured
+49.93 for +50), whatever the SFZ's bend range, and timing is untouched.
"""

from __future__ import annotations

import contextlib
import ctypes as C
import os
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from .base import Engine, EngineError

LIB_ENV = "PIANO_RENDER_SFIZZ_LIB"
SFIZZ_PROCESS_LIVE = 0
SFIZZ_PROCESS_FREEWHEELING = 1
SAMPLE_QUALITY = 2
NUM_VOICES = 256
# Whole samples are loaded up front. With sfizz's default preload (8192 frames), a voice whose background load
# hadn't been picked up by the time its first 0.17 s ran out simply stopped (3 notes in 60 fresh loads, 22 Oct),
# even in freewheeling mode, and a truncated note would be cached for good. Cost for the Salamander V3 SFZ: 19 s
# to load, 2.5 GB resident (vs 0.8 s / 93 MB). $PIANO_RENDER_SFIZZ_PRELOAD (frames; 0 = sfizz's default) overrides.
PRELOAD_ENV = "PIANO_RENDER_SFIZZ_PRELOAD"
PRELOAD: Optional[int] = 1 << 30
# Streaming (engine.realtime = True, piano_render/stream.py): live mode (no freewheeling) and only the start of each
# sample preloaded; the rest is read by sfizz's background loader while the voice plays its preloaded start. The
# streamer renders at playback pace, so the loader gets the preload's own length in wall time per fresh note
# (measured in piano_render/README.md, "Latency and compute"). $PIANO_RENDER_SFIZZ_STREAM_PRELOAD (frames) overrides.
STREAM_PRELOAD_ENV = "PIANO_RENDER_SFIZZ_STREAM_PRELOAD"
STREAM_PRELOAD: Optional[int] = 32768
# ...and the stream never renders faster than STREAM_PRELOAD / LOADER_BUDGET_S times real time (max_speed), so every
# fresh note leaves the loader at least LOADER_BUDGET_S of wall time, also while the stream fills its buffer at the
# start. Measured 23 Oct (20 s excerpts paced at 1x, a fresh process each): with sfizz's own 8192 frames and an
# unpaced fill, 20 of 52 loads cut notes (mostly in the first 0.6 s, the fill; some at 1.3 s and 15.7 s); with
# 32768, 0 of 120, every run bit-identical to a 0.25x-paced one and to freewheeling.
LOADER_BUDGET_S = 0.3
A4_HZ = 440.0

_P = C.c_void_p
_FLOATP = C.POINTER(C.c_float)
_SIGS = [
    ("sfizz_create_synth", [], _P),
    ("sfizz_free", [_P], None),
    ("sfizz_load_file", [_P, C.c_char_p], C.c_bool),
    ("sfizz_set_sample_rate", [_P, C.c_float], None),
    ("sfizz_set_samples_per_block", [_P, C.c_int], None),
    ("sfizz_set_num_voices", [_P, C.c_int], None),
    ("sfizz_set_sample_quality", [_P, C.c_int, C.c_int], None),
    ("sfizz_enable_freewheeling", [_P], None),
    ("sfizz_set_tuning_frequency", [_P, C.c_float], None),
    ("sfizz_send_hd_note_on", [_P, C.c_int, C.c_int, C.c_float], None),
    ("sfizz_send_hd_note_off", [_P, C.c_int, C.c_int, C.c_float], None),
    ("sfizz_send_cc", [_P, C.c_int, C.c_int, C.c_int], None),
    ("sfizz_send_pitch_wheel", [_P, C.c_int, C.c_int], None),
    ("sfizz_all_sound_off", [_P], None),
    ("sfizz_render_block", [_P, C.POINTER(_FLOATP), C.c_int, C.c_int], None),
    ("sfizz_get_num_active_voices", [_P], C.c_int),
    ("sfizz_get_num_regions", [_P], C.c_int),
    ("sfizz_get_num_preloaded_samples", [_P], C.c_int),
    ("sfizz_set_preload_size", [_P, C.c_uint], None),
    ("sfizz_get_preload_size", [_P], C.c_uint),
]

_lib_cache: dict[str, C.CDLL] = {}


def find_library(engines_root: "str | Path") -> Optional[Path]:
    env = os.environ.get(LIB_ENV)
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    base = Path(engines_root).expanduser() / "sfizz"
    if not base.is_dir():
        return None
    found = sorted(base.rglob("libsfizz.so*"), key=lambda p: (len(p.parts), p.name != "libsfizz.so.1", str(p)))
    found = [p for p in found if p.is_file()]
    return found[0] if found else None


def _load_cdll(path: Path) -> C.CDLL:
    key = str(path.resolve())
    if key in _lib_cache:
        return _lib_cache[key]
    tree = path.resolve().parent
    # walk up to the "sfizz" directory so dependencies unpacked beside it (e.g. usr/lib/x86_64-linux-gnu) are found
    for parent in [tree, *tree.parents]:
        if parent.name == "sfizz":
            tree = parent
            break
    tried: set[str] = set()
    for _ in range(16):
        try:
            lib = C.CDLL(key, mode=C.RTLD_GLOBAL)
            break
        except OSError as e:
            m = re.search(r"(\S+\.so[\.\d]*): cannot open shared object file", str(e))
            if not m or m.group(1) in tried:
                raise EngineError(f"cannot load {key}: {e}") from e
            dep = m.group(1)
            tried.add(dep)
            hits = [p for p in tree.rglob(dep) if p.is_file() or p.is_symlink()]
            if not hits:
                raise EngineError(f"cannot load {key}: {dep} is missing (not on the system or under {tree})") from e
            C.CDLL(str(hits[0].resolve()), mode=C.RTLD_GLOBAL)
    else:
        raise EngineError(f"cannot load {key}: too many missing dependencies")
    for name, args, res in _SIGS:
        f = getattr(lib, name)
        f.argtypes = args
        f.restype = res
    _lib_cache[key] = lib
    return lib


@contextlib.contextmanager
def _stdout_to_stderr():
    """libsfizz prints chatter ("Enabling freewheeling", "Changing default sample path") on fd 1 while it loads;
    send it to fd 2 so a CLI's stdout (e.g. --json) stays clean."""
    try:
        sys.stdout.flush()
        saved = os.dup(1)
    except (OSError, ValueError):
        yield
        return
    try:
        os.dup2(2, 1)
        yield
    finally:
        C.CDLL(None).fflush(None)
        os.dup2(saved, 1)
        os.close(saved)


class SfizzEngine(Engine):
    name = "sfizz"
    block = 512

    def __init__(self, inst: dict, engines_root):
        super().__init__(inst, engines_root)
        self._synth: Optional[int] = None
        self._lib: Optional[C.CDLL] = None
        self._bufs = (_FLOATP * 2)()
        f = inst.get("file")
        if not f:
            raise EngineError(f"instrument {inst.get('id')!r} has no SFZ file")
        self.file = Path(f)
        env = os.environ.get(PRELOAD_ENV)
        self.preload: Optional[int] = (int(env) or None) if env else PRELOAD

    @classmethod
    def available(cls, engines_root) -> bool:
        return find_library(engines_root) is not None

    @classmethod
    def version(cls, engines_root) -> str:
        p = find_library(engines_root)
        return f"sfizz:{p.resolve().name if p else 'missing'}"

    def _load(self, sr: int) -> None:
        with _stdout_to_stderr():
            self._load_quiet(sr)

    def _load_quiet(self, sr: int) -> None:
        if not self.file.exists():
            raise EngineError(f"SFZ not found: {self.file}")
        path = find_library(self.engines_root)
        if path is None:
            raise EngineError(f"libsfizz not found under {self.engines_root / 'sfizz'} (or ${LIB_ENV})")
        self.close()
        lib = self._lib = _load_cdll(path)
        s = lib.sfizz_create_synth()
        if not s:
            raise EngineError("sfizz_create_synth failed")
        self._synth = s
        lib.sfizz_set_sample_rate(s, float(sr))
        lib.sfizz_set_samples_per_block(s, self.block)
        voices = NUM_VOICES
        if self.realtime and self.inst.get("stream_voices"):
            voices = max(8, int(self.inst["stream_voices"]))          # instruments.json: a lighter live load
        lib.sfizz_set_num_voices(s, voices)
        lib.sfizz_set_sample_quality(s, SFIZZ_PROCESS_LIVE, SAMPLE_QUALITY)
        lib.sfizz_set_sample_quality(s, SFIZZ_PROCESS_FREEWHEELING, SAMPLE_QUALITY)
        if self.realtime:
            env = os.environ.get(STREAM_PRELOAD_ENV)
            preload = (int(env) or None) if env else STREAM_PRELOAD
        else:
            lib.sfizz_enable_freewheeling(s)
            preload = self.preload
        if preload is not None:
            lib.sfizz_set_preload_size(s, int(preload))
        if not lib.sfizz_load_file(s, str(self.file).encode()):
            raise EngineError(f"sfizz could not load {self.file}")
        regions = lib.sfizz_get_num_regions(s)
        if regions <= 0:
            raise EngineError(f"sfizz loaded no regions from {self.file}")
        self.stats.update(lib=str(path.resolve()), regions=regions,
                          preloaded_samples=lib.sfizz_get_num_preloaded_samples(s),
                          preload_frames=int(lib.sfizz_get_preload_size(s)), live_mode=bool(self.realtime),
                          voices=voices)

    def _reset(self) -> None:
        lib, s = self._lib, self._synth
        lib.sfizz_all_sound_off(s)
        for cc in (64, 66, 67):
            lib.sfizz_send_cc(s, 0, cc, 0)
        lib.sfizz_send_pitch_wheel(s, 0, 0)

    def _set_pitch(self, cents: float) -> None:
        self._lib.sfizz_set_tuning_frequency(self._synth, A4_HZ * 2.0 ** (cents / 1200.0))

    def _render_block(self, out_l: np.ndarray, out_r: np.ndarray, events) -> None:
        lib, s = self._lib, self._synth
        for off, e, v in events:
            if e.kind == "on":
                lib.sfizz_send_hd_note_on(s, off, e.num, v)
            elif e.kind == "off":
                lib.sfizz_send_hd_note_off(s, off, e.num, e.value / 127.0)
            else:
                lib.sfizz_send_cc(s, off, e.num, e.value)
        n = len(out_l)
        if n != self.block or not (out_l.flags.c_contiguous and out_r.flags.c_contiguous):
            raise EngineError("sfizz blocks must be whole, contiguous float32 views")
        self._bufs[0] = out_l.ctypes.data_as(_FLOATP)
        self._bufs[1] = out_r.ctypes.data_as(_FLOATP)
        lib.sfizz_render_block(s, self._bufs, 2, n)

    def _active_voices(self) -> int:
        return int(self._lib.sfizz_get_num_active_voices(self._synth))

    def max_speed(self, sr: int = 48000) -> Optional[float]:
        if not self.realtime:
            return None
        frames = self.stats.get("preload_frames") or 8192
        return max(1.0, frames / float(sr) / LOADER_BUDGET_S)

    def close(self) -> None:
        s, self._synth = getattr(self, "_synth", None), None
        if s and self._lib is not None:
            self._lib.sfizz_free(s)
        self._loaded_sr = None
