"""Validation and the effective-config dump of a deployment (python -m smconfig check | dump)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import (CAPABILITIES, CONFIG_DIR, DEFAULT_PORTS, Deployment, destinations, load_layered_yaml, read_data,
               rooms)
from .legacy import MOVES

MANIFEST_KEYS = {"id", "name", "description", "location", "capabilities", "ports", "default_room", "kiosk", "host"}

# Files a capability needs to start (relative to the deployment directory).
REQUIRED: dict[str | None, tuple[str, ...]] = {
    None: ("publish-destinations.json", "rooms.json"),
    "room": ("room/room.yaml", "room/modes.json"),
    "dmx": ("lights/patch.yaml",),
    "blinds": ("room/rf/blinds.json",),
    "sonos": ("room/sonos.json",),
    "dot": ("dot.json", "services/sm-dot.env"),
    "show": ("services/sm-show.env",),
    "live": ("services/sm-stream.env",),
}

SHOW_BASE = CONFIG_DIR / "room" / "show.yaml"


def known_files(dep: Deployment) -> list[str]:
    """Every deployment file smconfig knows of, existing or not, plus any device file in room/rf/."""
    rels = set(MOVES.values()) | {"deployment.yaml", "show.yaml"}
    rf = dep.path("room", "rf")
    if rf.is_dir():
        rels |= {f"room/rf/{p.name}" for p in rf.glob("*.json")}
    return sorted(rels)


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return None


def dump(dep: Deployment) -> dict[str, Any]:
    """Everything a process resolves from this deployment, in one stable JSON-able structure."""
    files = {}
    for rel in known_files(dep):
        p = dep.path(*rel.split("/"))
        files[rel] = {"exists": p.exists(), "sha1": _sha(p) if p.is_file() else None}
    out: dict[str, Any] = {
        "id": dep.id, "name": dep.name, "dir": str(dep.dir),
        "capabilities": dep.capabilities,
        "ports": dep.ports,
        "urls": {s: dep.url(s) for s in dep.ports},
        "location": dep.location,
        "default_room": dep.default_room,
        "kiosk": dep.kiosk,
        "host": dep.host,
        "files": files,
    }
    try:
        dests = destinations(dep)
        out["screens"] = {d["id"]: {"live": isinstance(d.get("live"), dict), "type": d.get("type"),
                                    "headless": bool(d.get("headless"))} for d in dests}
    except Exception as e:  # noqa: BLE001 - reported, not fatal, in a dump
        out["screens"] = {"_error": str(e)}
    try:
        out["rooms"] = {k: {kk: v.get(kk) for kk in ("name", "screens")} for k, v in rooms(dep).items()}
    except Exception as e:  # noqa: BLE001
        out["rooms"] = {"_error": str(e)}
    return out


def check(dep: Deployment) -> tuple[list[str], list[str]]:
    """(problems, notes). A deployment with problems must not be started."""
    problems: list[str] = []
    notes: list[str] = []
    m = dep.manifest
    for k in m:
        if k not in MANIFEST_KEYS:
            problems.append(f"deployment.yaml: unknown key {k!r} (known: {', '.join(sorted(MANIFEST_KEYS))})")
    for k in (m.get("capabilities") or {}):
        if k not in CAPABILITIES:
            problems.append(f"deployment.yaml: unknown capability {k!r}")
    for k in (m.get("ports") or {}):
        if k not in DEFAULT_PORTS:
            problems.append(f"deployment.yaml: unknown port {k!r} (known: {', '.join(DEFAULT_PORTS)})")
    ports = list(dep.ports.values())
    if len(set(ports)) != len(ports):
        problems.append(f"deployment.yaml: two services share a port: {dep.ports}")
    if m.get("location") and not dep.location:
        problems.append("deployment.yaml: location needs both lat and lon")
    caps = dep.capabilities
    if caps.get("art_director") and not caps.get("generation"):
        problems.append("capability art_director needs generation")

    for cap, rels in REQUIRED.items():
        if cap is not None and not caps.get(cap):
            continue
        for rel in rels:
            if not dep.path(*rel.split("/")).exists():
                problems.append(f"{rel}: missing (needed{' by ' + cap if cap else ''})")

    # Every file present must parse.
    for rel in known_files(dep):
        p = dep.path(*rel.split("/"))
        if p.is_file() and p.suffix in (".json", ".yaml", ".yml"):
            try:
                read_data(p)
            except Exception as e:  # noqa: BLE001
                problems.append(f"{rel}: does not parse: {e}")
    try:
        load_layered_yaml(SHOW_BASE, dep.path("show.yaml"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"show.yaml over config/room/show.yaml: {e}")

    # The room's modes through room_service's own model (when it is importable here): a modes.json that parses but
    # that room_service would reject (no '* -> *' rule, an unknown mode, a bad default) is a problem, not a surprise
    # at start-up. Device names are not checked here (no adapters): room_service warns about those itself.
    modes = dep.path("room", "modes.json")
    if caps.get("room") and modes.is_file():
        try:
            from room_service.roomstate.model import validate as _validate_modes  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - room_service not importable in this environment: skip, say so
            notes.append("room/modes.json: not checked against room_service's model (room_service not importable)")
        else:
            try:
                for err in _validate_modes(read_data(modes), []):
                    problems.append(f"room/modes.json{err.get('path') or ''}: {err.get('message')}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"room/modes.json: {e}")

    # Cross-references: one fact, one place, and everything that names a screen or room names a real one.
    try:
        ids = [d["id"] for d in destinations(dep)]
    except Exception as e:  # noqa: BLE001
        problems.append(f"publish-destinations.json: {e}")
        ids = []
    if len(ids) != len(set(ids)):
        problems.append("publish-destinations.json: duplicate ids")
    try:
        rms = rooms(dep)
    except Exception as e:  # noqa: BLE001
        problems.append(f"rooms.json: {e}")
        rms = {}
    for rid, r in rms.items():
        for s in r.get("screens") or []:
            if s not in ids:
                problems.append(f"rooms.json: room {rid!r} names screen {s!r}, which publish-destinations.json lacks")
    if dep.default_room and dep.default_room not in rms:
        problems.append(f"deployment.yaml: default_room {dep.default_room!r} is not in rooms.json")
    for s in (dep.kiosk.get("screens") or {}):
        if s not in ids:
            problems.append(f"deployment.yaml: kiosk screen {s!r} is not in publish-destinations.json")
    patch = dep.path("lights", "patch.yaml")
    if patch.is_file():
        try:
            for did, dev in ((read_data(patch) or {}).get("devices") or {}).items():
                s = (dev or {}).get("screen") if isinstance(dev, dict) else None
                if s and s not in ids:
                    problems.append(f"lights/patch.yaml: device {did!r} names screen {s!r}, which is not a destination")
        except Exception:  # noqa: BLE001 - already reported as a parse problem
            pass
    # ${VAR}s the service flags use must be set on this machine (scripts/ctl refuses to start a service otherwise).
    import re

    from . import env as _env

    for f in sorted(dep.path("services").glob("*.env")) if dep.path("services").is_dir() else []:
        for var in sorted(set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", "\n".join(
                ln for ln in f.read_text().splitlines() if not ln.lstrip().startswith("#"))))):
            if not var.startswith("PORT_") and not _env(var):
                notes.append(f"services/{f.name} uses ${{{var}}}, which is not set on this machine (.env); "
                             "scripts/ctl will refuse to start that service until it is")
    for cap in ("generation", "alexa", "tv_power", "kiosk_admin"):
        if not caps.get(cap):
            notes.append(f"{cap}: off")
    return problems, notes
