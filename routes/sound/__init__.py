"""routes.sound: /api/sound/* — sound outputs, their latency and its calibration, for every part of the app
(docs/SOUND.md; smsound is the shared plumbing)."""
from .api import sound_api  # noqa: F401
from .calibrate import calibrator  # noqa: F401
