"""Bearer token file: ~/.config/screen-machine/stream_service.token (64 hex chars, mode 0600).

Created on first start (O_EXCL temp file + hard link: atomic, and two starting processes cannot both create
one), never logged.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import secrets
import stat
from typing import Optional

log = logging.getLogger("stream_service.tokens")

DEFAULT_TOKEN_FILE = "~/.config/screen-machine/stream_service.token"
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def _create_atomically(path: pathlib.Path) -> Optional[str]:
    """Write a new token to a private temp file, then hard-link it into place: the file appears complete or
    not at all (a concurrent reader never sees it empty), and the link fails if the file already exists."""
    token = secrets.token_hex(32)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return None
        except OSError:  # a filesystem without hard links: plain O_EXCL create (still never overwrites)
            try:
                fd2 = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return None
            with os.fdopen(fd2, "w") as f:
                f.write(token + "\n")
    finally:
        tmp.unlink(missing_ok=True)
    return token


def load_or_create_token(path_str: str = DEFAULT_TOKEN_FILE) -> str:
    path = pathlib.Path(path_str).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        token = _create_atomically(path)
        if token is not None:
            log.info("created token file %s (mode 0600)", path)
            return token
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning("token file %s has mode %o; it should be 0600", path, mode)
    token = path.read_text().strip()
    if not _TOKEN_RE.match(token):
        raise SystemExit(f"token file {path} does not hold 64 hex characters")
    return token
