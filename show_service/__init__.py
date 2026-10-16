"""show_service: the room's own ears, art director and lights (docs/SHOW_ENGINE.md).

It listens to the room: audio and the Steinway's MIDI arrive as VBAN over UDP from senders it subscribes to (the
kiosk mini-PC, PC22), are placed on one CLOCK_MONOTONIC timeline by sample index, and become real-time features,
notes, harmony and a beat clock that it publishes on the show bus (``music.*``, §11.1) for the renderer, the lights
and the director. Module map and the interfaces between modules: show_service/README.md. Maintainer:
screen-machine-c8.
"""

__version__ = "0.2.0"          # 0.2: Stage 3 (the brain: director, Auto, the API, test sources, listener)
