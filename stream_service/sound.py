"""What a scene can hear: the piano (USB MIDI) and the room mic, per session (owner, 15 Oct; docs/SHOW_ENGINE.md).

A scene or effect class that declares ``listens = ("notes", "spectrum", "chroma")`` reads ``self.sound``, this
object, refreshed once per rendered frame on the render thread before any plug-in renders:

  sound.present      True while the piano is playing (or played recently); scenes must look alive without it
  sound.frame        sound_view.SoundFrame (host numpy): notes [128, 4], onsets, releases, pedals, chroma,
                     spectrum [32], history [256, 32] + history_row, key, chord, present, source
  sound.notes_gpu    the notes table on the session's GPU (cupy float32 [128, 4]: LEVEL, VEL, AGE_ON, AGE_OFF)
  sound.spectrum_gpu the 32-band spectrum on the GPU (cupy float32 [32])

Ages are evaluated at photon time (the frame's time plus the session's measured lead), so what a scene draws for
a note matches what the ear hears at the TV.  Read-only for plug-ins; both scenes of a transition see the same
frame.  Every music.* and show.state message reaches each session's view (``on_message``, any thread, append only).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("stream_service.sound")


def available() -> bool:
    try:
        from . import sound_view  # noqa: F401
        return True
    except Exception:
        return False


class Sound:
    def __init__(self) -> None:
        from .sound_view import SoundView

        self.view = SoundView()
        self.frame: Any = self.view.frame
        self.notes_gpu = None
        self.spectrum_gpu = None
        self._failed = False

    @property
    def present(self) -> bool:
        return bool(self.frame is not None and getattr(self.frame, "present", False))

    @property
    def spectrum_live(self) -> bool:
        """The spectrum is the room's (music.features fresh), piano or not (d4's G6)."""
        v = self.view
        ft, t = getattr(v, "features_t", None), getattr(v, "t", None)
        return ft is not None and t is not None and t - ft <= 1000.0

    def on_message(self, msg: dict) -> None:
        self.view.on_message(msg)

    def alloc(self, cp) -> None:
        """Worker thread, on the session's device: the GPU copies (never allocated on the render thread, R20)."""
        if self.notes_gpu is None:
            self.notes_gpu = cp.zeros((128, 4), cp.float32)
            self.spectrum_gpu = cp.zeros((32,), cp.float32)

    def update(self, photon_s: float) -> None:
        """Render thread, once per frame, before any plug-in renders.  Never raises into the frame."""
        if self._failed:
            return
        try:
            self.frame = f = self.view.update(photon_s * 1000.0)
            if self.notes_gpu is not None:
                self.notes_gpu.set(f.notes)                       # 2 KB on the current (render) stream
                spec = getattr(f, "spectrum", None)
                if spec is not None:
                    self.spectrum_gpu.set(spec)
        except Exception:
            self._failed = True
            log.exception("sound view failed; scenes hear nothing from here on")


def new_sound() -> Optional[Sound]:
    """A session's Sound, or None while sound_view.py is not installed."""
    if not available():
        return None
    try:
        return Sound()
    except Exception:
        log.exception("could not create the sound view")
        return None
