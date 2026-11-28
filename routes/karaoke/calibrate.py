"""Karaoke's speaker-delay calibration is now the app's (docs/SOUND.md, 27 Nov): the chirps and their measurement are
smsound.chirps, a run is routes/sound/calibrate.py (any screen or Sonos), and the result is the output's delay in
smsound.latency, which the karaoke player reads (Store.settings_view). This module keeps the names the tests use."""
from smsound.chirps import (CHIRP_S, CHIRPS, MAX_DELAY_S, MIN_DELAY_S, OFFSETS_S, SETTLE,  # noqa: F401
                            assemble, chirp, chirp_specs, measure)
