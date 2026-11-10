"""Where each deployment file lived before deployments/ existed (up to 28 Oct 2026), and the move to its new home.

Used by scripts/migrate_deployment.py on a machine that ran the old layout, and by the tests that prove the move
lost nothing. Paths are repo-relative; the new side is relative to deployments/<id>/.
"""
from __future__ import annotations

# old repo path -> path inside deployments/<id>/
MOVES: dict[str, str] = {
    "routes/data/publish-destinations.json": "publish-destinations.json",
    "routes/data/rooms.json": "rooms.json",
    "routes/data/intensity.json": "intensity.json",
    "config/room/dot.json": "dot.json",
    "config/room/room.prod.yaml": "room/room.yaml",
    "config/room/modes.json": "room/modes.json",
    "config/room/auto_hints.json": "room/auto_hints.json",
    "config/room/sonos.json": "room/sonos.json",
    "config/room/tv.json": "room/tv.json",
    "config/room/patch.yaml": "lights/patch.yaml",
    "config/room/fixtures.yaml": "lights/fixtures.yaml",
    "config/room/fixtures.added.yaml": "lights/fixtures.added.yaml",
    "config/room/fixtures.setup.yaml": "lights/fixtures.setup.yaml",
    "config/room/space.yaml": "lights/space.yaml",
    "config/room/space.added.yaml": "lights/space.added.yaml",
    "config/room/latency.json": "calibration/latency.json",
    "config/room/audio_cal.json": "calibration/audio_cal.json",
    "config/live/stream_switches.json": "live/stream_switches.json",
    "deploy/sm-stream.env": "services/sm-stream.env",
    "deploy/sm-show.env": "services/sm-show.env",
    "deploy/sm-dot.env": "services/sm-dot.env",
}

# Directories whose device files (one JSON per device; the app adds new ones) move whole. rf/types/ (the shared
# device-type definitions) stays in config/room/rf/types/.
DIR_MOVES: dict[str, str] = {
    "config/room/rf": "room/rf",
}
DIR_MOVE_EXCLUDE = {"config/room/rf/types"}

# The site layer of config/room/show.yaml: these keys moved out of the shared file into deployments/<id>/show.yaml,
# which smconfig.load_layered_yaml deep-merges back over it. Dotted paths; a mapping key moves whole.
SHOW_SITE_KEYS: tuple[str, ...] = (
    "senders.kiosk",
    "senders.pc22",
    "situations.midi.streams",
    "situations.sonos.now_playing",
    "situations.sonos.volume",
    "situations.light.sensor",
    "lights.flow.mirror_ball.enabled",
    "lights.flow.mirror_ball.power_member",
    "lights.flow.mirror_ball.direction_target",
    "lights.flow.mirror_ball.speed_target",
    "lights.groups.remote-lights",          # PC22, 28 Oct: the glitterball and pinspots as appliances (their room
    "lights.appliances",                    # targets), merged in after the layer was cut
    "ensemble.players.zones",
)

# Data edits made to moved files on purpose (new path -> list of (destination id, dotted key, value)). Each one replaces
# a hard-coded fact the code used to carry (stream_service's DEFAULT_FIXTURES gave devtest north's lights). The
# migration's proof applies them to the old content before comparing. Comment-only changes and ${VAR} references that
# expand to the old value need no entry: the proof compares parsed data and expanded env lines.
CONTENT_EDITS: dict[str, list[tuple[str, str, object]]] = {
    "publish-destinations.json": [("devtest", "live.fixture", "screen.north")],
}

# Secrets that used to sit in the repo root (tracked by mistake). They move to ~/.config/screen-machine/<new>.
SECRET_MOVES: dict[str, str] = {
    "north-screen-token.txt": "samsung/north-screen-token.txt",
    "south-screen-token.txt": "samsung/south-screen-token.txt",
    "north-screen-token-4.txt": "samsung/north-screen-token-4.txt",   # unused spares, kept with the others
    "south-screen-token-4.txt": "samsung/south-screen-token-4.txt",
}

# The symlink src/data/publish-destinations.json -> routes/data/publish-destinations.json is removed: nothing in
# src/ imports it, and it made Flask's findfile() see a second copy of the screens.
REMOVED_LINKS = ("src/data/publish-destinations.json",)
