"""piano_render: MIDI file + instrument id -> a rendered, mastered audio file (offline, cached).

Replaces the test piano's Windows GS Wavetable synth with sampled pianos (sfizz for SFZ libraries, FluidSynth for
SF2s). Engines are pluggable (piano_render.engines): a paid engine (Pianoteq, a VST3 host) is one more module.

    from piano_render import render
    res = render.render("piece.mid", "salamander")      # -> RenderResult (path to a 24-bit 48 kHz FLAC)

See piano_render/README.md for the contract and the CLI (python -m piano_render ...).
"""

from __future__ import annotations

import os as _os

# This box is shared with two live 4K streams and show_service's realtime receiver: numeric libraries stay
# single-threaded unless the caller has said otherwise.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

__all__ = ["instruments", "score", "engines", "realism", "master", "render"]
