"""What a scene can hear: the piano (USB MIDI) and the room mic, per session (owner, 15 Oct; docs/SHOW_ENGINE.md).

A scene or effect class that declares ``listens = ("notes", "spectrum", "chroma")`` reads ``self.sound``, this
object, refreshed once per rendered frame on the render thread before any plug-in renders:

  sound.present      True while the piano is playing (or played recently); scenes must look alive without it
  sound.frame        sound_view.SoundFrame (host numpy): notes [128, 4], onsets, releases, pedals, chroma,
                     spectrum [32], history [256, 32] + history_row, key, chord, present, source
  sound.notes_gpu    the notes table on the session's GPU (cupy float32 [128, 4]: LEVEL, VEL, AGE_ON, AGE_OFF)
  sound.spectrum_gpu the 32-band spectrum on the GPU (cupy float32 [32])

The fast feed (listens "scope" / "fft"; scope_feed.py, 18 Oct): show_service's raw waveform and 48-band FFT at up
to 60 frames a second, shared by the process (``feed``), stepped per session by a ScopeState here:

  sound.wave_gpu         cupy float32 [1024]  the waveform, DC removed, AGC'd, soft-clipped: about -1..1
  sound.wave_trigger     int: the latest rising zero crossing in the first half (a stable scope start), 0 if none
  sound.fft_gpu          cupy float32 [48]    0..1, log bands 40 Hz-16 kHz, instant attack, release tau 0.12 s
  sound.fft_peak_gpu     cupy float32 [48]    peak hold 0.4 s, then falls 1.5 / s
  sound.fft_history_gpu  cupy float32 [128, 48] one row per 1/30 s; sound.fft_row is the newest row
  sound.level            0..1 fast loudness;  sound.scope_live: the fast feed is fresh (< 0.5 s)
  sound.wave / fft / fft_peak / fft_history   the same, host numpy (read-only)
Without the fast feed, fft follows the smoothed spectrum32 and the wave eases to zeros.

The room ensemble's register (docs/ENSEMBLE.md §8.6; register_split: low notes on one side, high on the other):

  sound.register     None, or this screen's window (lo, hi, outside): lo..hi of register 0 (A0) .. 1 (C8).  A plug-in
                     that draws notes draws those inside the window at full and the others at ``outside`` (0..1;
                     never hidden): ``stream_service.ensemble.note_weights(sound.register, pitches)`` gives the
                     per-pitch weights (None: all at full).  Set by the engine each frame from the session's levers.
  sound.segment      None, or this screen's place in one picture across the room's screens (owner, 22 Oct: a score
                     scene's one tape, notes entering on the right-hand screen and flowing into the left): (k, n), k
                     its index among the ensemble's screens ordered by their players' x (0 the leftmost), n how many
                     screens.  None: no ensemble, or this screen is its only one (draw the whole picture).  Set by
                     the engine each frame (``stream_service.ensemble.segment``, from room.direction.ensemble).

Ages are evaluated at photon time (the frame's time plus the session's measured lead), so what a scene draws for
a note matches what the ear hears at the TV.  Read-only for plug-ins; both scenes of a transition see the same
frame.  Every music.* and show.state message reaches each session's view (``on_message``, any thread, append only).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .scope_feed import ScopeFields, ScopeState

log = logging.getLogger("stream_service.sound")


def available() -> bool:
    try:
        from . import sound_view  # noqa: F401
        return True
    except Exception:
        return False


class Sound(ScopeFields):
    def __init__(self, feed: Any = None) -> None:
        from .sound_view import SoundView

        self.view = SoundView()
        self.frame: Any = self.view.frame
        self.notes_gpu = None
        self.spectrum_gpu = None
        self._failed = False
        self.feed = feed                      # scope_feed.ScopeFeed (process-wide) or None
        self.register: Optional[tuple] = None  # the ensemble's register window (lo, hi, outside), or None (all full)
        self.segment: Optional[tuple] = None   # (k, n): this screen's place across the ensemble's screens, or None
        self.scope = ScopeState()
        self._scope_t: Optional[float] = None
        self._scope_errors = 0
        self._scope_logged = -1e9

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
        self.scope_alloc(cp)

    def update(self, photon_s: float) -> None:
        """Render thread, once per frame, before any plug-in renders.  Never raises into the frame."""
        if not self._failed:
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
        self._scope_step()

    def _scope_step(self) -> None:
        """Render thread: the fast feed's newest frame (a lock-free read) through this session's ScopeState.  An error
        skips this frame's step (logged at most once a minute) and the next frame tries again: no latch."""
        try:
            now = time.monotonic()
            dt = 0.0 if self._scope_t is None else now - self._scope_t
            self._scope_t = now
            feed = self.feed
            spec = None if self._failed else getattr(self.frame, "spectrum", None)
            self.scope.update(now, feed.latest if feed is not None else None, spec, dt)
            self.scope_upload()
        except Exception:
            self._scope_errors += 1
            t = time.monotonic()
            if t - self._scope_logged >= 60.0:
                self._scope_logged = t
                log.exception("scope step failed (%d so far); scope / fft effects keep their last values this frame",
                              self._scope_errors)


def new_sound(feed: Any = None) -> Optional[Sound]:
    """A session's Sound, or None while sound_view.py is not installed.  ``feed``: the process's ScopeFeed."""
    if not available():
        return None
    try:
        return Sound(feed=feed)
    except Exception:
        log.exception("could not create the sound view")
        return None
