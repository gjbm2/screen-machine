"""Validation and the effective-config dump of a deployment (python -m smconfig check | dump)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import (CAPABILITIES, CONFIG_DIR, DEFAULT_PORTS, Deployment, destinations, load_layered_yaml, read_data,
               rooms)
from .legacy import MOVES

MANIFEST_KEYS = {"id", "name", "description", "location", "capabilities", "ports", "default_room", "kiosk", "host",
                 "art_director", "wording"}

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

# The manifest's wording keys (Deployment.words(key, generic default)): who reads each. A key not listed here is a
# typo (it would silently fall back to the generic phrase); a new one goes here and in deployments/README.md.
WORDING_KEYS = {
    "art_screen": "Flask image generator: the screen the art is made for",
    "art_space": "Flask image generator: the space it hangs in",
    "art_library": "Flask art tagger: the library's name",
    "art_screens": "Flask art tagger rubric (config/art_tags.yaml): the screens the art hangs on",
    "listen_space": "show_service listener prompt: the room the mic hears",
    "screen_device": "room_service page copy: what a screen is called (TV pages, Show details on the TV)",
    "lights_menu": "the app menu's Lights hint",
    "windows_host": "show_service: the Windows host's name in piano and Sonos copy",
    "windows_speakers": "show_service: the Windows host's speakers",
    "artnet_no_replies": "show_service: the Art-Net 'no replies yet' hint",
}


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


def _or_empty(fn, dep: Deployment) -> Any:
    try:
        return fn(dep)
    except Exception:  # noqa: BLE001 - reported by the cross-reference checks
        return None


def _screen_warning(item: dict) -> bool:
    """A modes.json lint warning about the screens, page or diag subsystems (the ones validate_site_modes models from
    publish-destinations.json); warnings about devices only say that check has no device adapters."""
    if item.get("code") == "unknown_subsystem":
        return False
    key = str(item.get("path") or "").rstrip("/").rsplit("/", 1)[-1]
    sub = key.split(":", 1)[-1].split(".", 1)[0]
    return sub in ("screens", "page", "diag")


def _as_list(v: Any) -> list[str] | None:
    """A list of ids (a lone string counts as one), [] when absent, None when it is neither."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    return None


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
    wording = m.get("wording")
    if wording is not None and not isinstance(wording, dict):
        problems.append("deployment.yaml: wording must be a mapping of key: phrase")
    for k, v in (wording or {}).items() if isinstance(wording, dict) else ():
        if k not in WORDING_KEYS:
            problems.append(f"deployment.yaml: unknown wording key {k!r} (known: {', '.join(sorted(WORDING_KEYS))})")
        elif not isinstance(v, str) or not v.strip():
            problems.append(f"deployment.yaml: wording.{k} must be a non-empty string")
    kiosk_screens = (m.get("kiosk") or {}).get("screens") if isinstance(m.get("kiosk"), dict) else None
    if kiosk_screens is not None and not isinstance(kiosk_screens, dict):
        problems.append("deployment.yaml: kiosk.screens must be a mapping of screen id: {name, output, cdp_port}")

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
    modes_raw: Any = None
    if caps.get("room") and modes.is_file():
        try:
            from room_service.room_config import validate_site_modes  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - room_service not importable in this environment: skip, say so
            notes.append("room/modes.json: not checked against room_service's model (room_service not importable)")
        else:
            try:
                modes_raw = read_data(modes)
                for item in validate_site_modes(modes_raw, _or_empty(rooms, dep), _or_empty(destinations, dep),
                                                warnings=True):
                    where = f"room/modes.json{item.get('path') or ''}: {item.get('message')}"
                    if item.get("level") != "warning":
                        problems.append(where)
                    elif _screen_warning(item):     # only the screens are modelled here; devices are placeholders
                        notes.append(where)
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
    for s in (kiosk_screens if isinstance(kiosk_screens, dict) else {}):
        if s not in ids:
            problems.append(f"deployment.yaml: kiosk screen {s!r} is not in publish-destinations.json")
    ad = m.get("art_director")
    if ad is not None:
        if not isinstance(ad, dict):
            problems.append("deployment.yaml: art_director must be a mapping {modes: [...], rooms: [...]}")
            ad = {}
        for k in ad:
            if k not in ("modes", "rooms"):
                problems.append(f"deployment.yaml: art_director: unknown key {k!r} (known: modes, rooms)")
        ad_modes, ad_rooms = _as_list(ad.get("modes")), _as_list(ad.get("rooms"))
        if ad_modes is None or ad_rooms is None:
            problems.append("deployment.yaml: art_director.modes and .rooms are lists of ids")
        else:
            known_modes = set(((modes_raw or {}).get("modes") or {})) if isinstance(modes_raw, dict) else set()
            for md in ad_modes:
                if known_modes and md not in known_modes:
                    problems.append(f"deployment.yaml: art_director mode {md!r} is not in room/modes.json")
            for r in ad_rooms:
                if r not in rms:
                    problems.append(f"deployment.yaml: art_director room {r!r} is not in rooms.json")
            if caps.get("art_director"):
                if not ad_modes:
                    notes.append("art_director: on, but art_director.modes is empty, so it never makes art")
                if not any((rms.get(r) or {}).get("screens") for r in (ad_rooms or list(rms))):
                    notes.append("art_director: on, but none of its rooms has screens")
    elif caps.get("art_director"):
        notes.append("art_director: on, but deployment.yaml has no art_director.modes, so it never makes art")
    try:
        for d in destinations(dep):
            live = d.get("live")
            if isinstance(live, dict) and not live.get("profile"):
                notes.append(f"publish-destinations.json: {d['id']}'s live block names no profile; room_service "
                             "picks one by its size (hevc-1080p30 up to 1920x1080, else hevc-2160p30)")
    except Exception:  # noqa: BLE001 - already reported above
        pass
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
