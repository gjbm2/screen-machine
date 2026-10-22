"""FluidSynth engine: SF2 soundfonts through pyfluidsynth on the system libfluidsynth (apt libfluidsynth3, 2.3.4).

Driven event by event, in process: render up to an event's frame (fluid_synth_write_float, float32, no 16-bit
rounding), send it, carry on. FluidSynth applies events at its internal 64-frame block boundaries, so an event can
land up to 63 frames (1.3 ms at 48 kHz) late; nothing is ever early. Its built-in reverb and chorus are off (the
realism stage owns the room), interpolation is the highest it has (7th order), 512 voices, one core, sample memory
not mlock'ed, synth.gain 1.0 (unity: the instrument's gain_db does the level). FluidSynth has no release velocity.

pitch_cents: RPN 0 via fluid_synth_pitch_wheel_sens (semitones = ceil(|cents| / 100), at least 1), then a pitch
bend of round(cents / (100 * range) * 8192); timing is untouched.
"""

from __future__ import annotations

import ctypes as C
import math
from pathlib import Path
from typing import Optional

import numpy as np

from .base import Engine, EngineError

SYSTEM_LIB = "/lib/x86_64-linux-gnu/libfluidsynth.so.3"
POLYPHONY = 512
INTERP_HIGHEST = 7
GAIN = 1.0

_fs_mod = None


def _fluidsynth():
    """Import pyfluidsynth once (it dlopens libfluidsynth at import) and add the bindings it lacks."""
    global _fs_mod
    if _fs_mod is None:
        import fluidsynth as fs  # noqa: PLC0415

        fl = fs._fl
        fl.fluid_synth_write_float.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_int,
                                               C.c_void_p, C.c_int, C.c_int]
        fl.fluid_synth_write_float.restype = C.c_int
        fl.fluid_synth_pitch_wheel_sens.argtypes = [C.c_void_p, C.c_int, C.c_int]
        fl.fluid_synth_pitch_wheel_sens.restype = C.c_int
        fl.fluid_synth_set_interp_method.argtypes = [C.c_void_p, C.c_int, C.c_int]
        fl.fluid_synth_set_interp_method.restype = C.c_int
        fl.fluid_version_str.argtypes = []
        fl.fluid_version_str.restype = C.c_char_p
        _fs_mod = fs
    return _fs_mod


class FluidSynthEngine(Engine):
    name = "fluidsynth"
    block = 512

    def __init__(self, inst: dict, engines_root):
        super().__init__(inst, engines_root)
        f = inst.get("file")
        if not f:
            raise EngineError(f"instrument {inst.get('id')!r} has no SF2 file")
        self.file = Path(f)
        preset = inst.get("preset") or {}
        self.bank = int(preset.get("bank", 0))
        self.program = int(preset.get("program", 0))
        self._syn = None
        self._sfid: Optional[int] = None

    @classmethod
    def available(cls, engines_root) -> bool:
        try:
            _fluidsynth()
            return True
        except Exception:
            return False

    @classmethod
    def version(cls, engines_root) -> str:
        try:
            return "fluidsynth:" + _fluidsynth()._fl.fluid_version_str().decode()
        except Exception:
            return "fluidsynth:missing"

    def _load(self, sr: int) -> None:
        if not self.file.exists():
            raise EngineError(f"SF2 not found: {self.file}")
        fs = _fluidsynth()
        self.close()
        # settings go in before new_fluid_synth (Synth's **kwargs), so polyphony, effects and threads take effect
        settings = {"synth.polyphony": POLYPHONY, "synth.reverb.active": 0, "synth.chorus.active": 0,
                    "synth.cpu-cores": 1, "synth.lock-memory": 0, "synth.dynamic-sample-loading": 0}
        syn = fs.Synth(gain=GAIN, samplerate=float(sr), channels=16, **settings)
        self._syn = syn
        sfid = syn.sfload(str(self.file))
        if sfid < 0:
            raise EngineError(f"FluidSynth could not load {self.file}")
        self._sfid = sfid
        if syn.program_select(0, sfid, self.bank, self.program) != 0:
            raise EngineError(f"{self.file.name} has no preset {self.bank}:{self.program}")
        fs._fl.fluid_synth_set_interp_method(syn.synth, -1, INTERP_HIGHEST)
        self.stats.update(lib=self.version(self.engines_root), preset=f"{self.bank}:{self.program}")

    def _reset(self) -> None:
        syn = self._syn
        syn.all_sounds_off(0)
        for cc in (64, 66, 67):
            syn.cc(0, cc, 0)
        syn.pitch_bend(0, 0)
        syn.program_select(0, self._sfid, self.bank, self.program)

    def _set_pitch(self, cents: float) -> None:
        syn = self._syn
        if cents == 0.0:
            syn.pitch_bend(0, 0)
            return
        rng = max(1, int(math.ceil(abs(cents) / 100.0 - 1e-9)))
        _fluidsynth()._fl.fluid_synth_pitch_wheel_sens(syn.synth, 0, rng)
        syn.pitch_bend(0, int(round(cents / (100.0 * rng) * 8192)))

    def _write(self, out_l: np.ndarray, out_r: np.ndarray, a: int, b: int) -> None:
        if b <= a:
            return
        fl = _fluidsynth()._fl
        lp = out_l.ctypes.data + 4 * a
        rp = out_r.ctypes.data + 4 * a
        if fl.fluid_synth_write_float(self._syn.synth, b - a, lp, 0, 1, rp, 0, 1) != 0:
            raise EngineError("fluid_synth_write_float failed")

    def _render_block(self, out_l: np.ndarray, out_r: np.ndarray, events) -> None:
        syn = self._syn
        pos = 0
        for off, e, v in events:
            self._write(out_l, out_r, pos, off)
            pos = max(pos, off)
            if e.kind == "on":
                syn.noteon(0, e.num, min(127, max(1, int(round(v * 127)))))
            elif e.kind == "off":
                syn.noteoff(0, e.num)
            else:
                syn.cc(0, e.num, e.value)
        self._write(out_l, out_r, pos, len(out_l))

    def _active_voices(self) -> int:
        return int(self._syn.get_active_voice_count())

    def close(self) -> None:
        syn, self._syn = getattr(self, "_syn", None), None
        if syn is not None:
            try:
                syn.delete()
            except Exception:
                pass
        self._loaded_sr = None
