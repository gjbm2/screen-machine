"""Config: device addresses in the repo (config/room/rf/), keys outside it."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RF_CONFIG = REPO / "config" / "room" / "rf"
SECRETS = Path.home() / ".config" / "screen-machine" / "rf"


def load_blinds() -> dict:
    return json.loads((RF_CONFIG / "blinds.json").read_text())


def save_blinds(cfg: dict) -> None:
    (RF_CONFIG / "blinds.json").write_text(json.dumps(cfg, indent=2) + "\n")


def load_hub_key() -> str | None:
    """~/.config/screen-machine/rf/bloc_hub.json: {"key": "<16 chars>"}, mode 0600."""
    path = SECRETS / "bloc_hub.json"
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"{path} is mode {mode:o}; run: chmod 600 {path}")
    key = json.loads(path.read_text()).get("key", "").strip()
    if len(key) != 16:
        raise ValueError(f"{path}: key must be 16 characters, got {len(key)}")
    return key


def save_hub_key(key: str) -> Path:
    SECRETS.mkdir(parents=True, exist_ok=True)
    path = SECRETS / "bloc_hub.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"key": key}, f)
    os.chmod(path, 0o600)
    return path
