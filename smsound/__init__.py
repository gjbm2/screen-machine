"""smsound: where sound plays and how late it is heard, for every part of the app (docs/SOUND.md).

* ``smsound.outputs``  the one vocabulary and list of sound outputs (``screen:<id>``, ``pc22``, ``sonos:<zone>``,
                       ``device:<id>``, ``none``) and the row every picker shows.
* ``smsound.latency``  the one store (the deployment's calibration/latency.json) and sign rule for output delays, and
                       ``overview()`` of every latency the deployment knows.
* ``smsound.chirps``   the measurement: coded chirps timed with the room mic (pure functions; Flask's
                       routes/sound/calibrate.py runs it).
"""
from . import latency, outputs  # noqa: F401
