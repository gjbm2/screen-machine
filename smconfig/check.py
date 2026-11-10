"""Validation and the effective-config dump of a deployment (python -m smconfig check | dump)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import (CAPABILITIES, CONFIG_DIR, DEFAULT_PORTS, Deployment, default_room, destinations, load_layered_yaml,
               own_mode_rooms, read_data, room_modes_path, rooms)
from . import _ROOM_ID_RE as _ROOM_FILE_ID   # a room id usable in room/modes.<room>.json
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
    "ad_room": "show_service treatment prompt: the room the AI art-directs",
    "ad_rig": "show_service treatment prompt: what is in that room (screens, lights, appliances)",
    "site_address": "Flask's Alexa overlays: the address under the QR (no line when unset)",
    "feel_room": "show_service feels: {feel_room} in a config/room/vibes.yaml persona",
}


# The frontend's build-time addresses: `npx vite build` bakes these .env values into dist/. Each one's manifest port,
# and the capability that needs it (None: always). VITE_API_URL: src/shared/http/client.ts (every API call, the TV
# pages' masks and files too). VITE_WS_HOST: src/pages/DisplayPage/hooks/useOverlayWebSocket.ts (the TV pages'
# overlay socket). VITE_ROOM_URL: src/pages/DisplayPage/index.tsx (the TV pages' control socket, which
# DisplayController opens) and src/pages/Room/api.ts (the Room page).
FRONTEND_ADDRESSES: dict[str, tuple[str, str | None]] = {
    "VITE_API_URL": ("flask", None),
    "VITE_WS_HOST": ("overlay_ws", None),
    "VITE_ROOM_URL": ("room", "room"),
}


def known_files(dep: Deployment) -> list[str]:
    """Every deployment file smconfig knows of, existing or not, plus any device file in room/rf/."""
    rels = set(MOVES.values()) | {"deployment.yaml", "show.yaml"}
    rf = dep.path("room", "rf")
    if rf.is_dir():
        rels |= {f"room/rf/{p.name}" for p in rf.glob("*.json")}
    try:                                         # each own_modes room's modes file (rooms.json problems: check says)
        rels |= {f"room/modes.{r}.json" for r in own_mode_rooms(dep) if _ROOM_FILE_ID.match(r)}
    except Exception:  # noqa: BLE001
        pass
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
        out["rooms"] = {k: {**{kk: v.get(kk) for kk in ("name", "screens")},
                            **{kk: v[kk] for kk in ("own_modes", "show") if kk in v}}    # only when set
                        for k, v in rooms(dep).items()}
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


def _validator(rel: str, notes: list[str]):
    """room_service's validate_site_modes, or None (said in a note) when room_service isn't importable here."""
    try:
        from room_service.room_config import validate_site_modes  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - room_service not importable in this environment: skip, say so
        notes.append(f"{rel}: not checked against room_service's model (room_service not importable)")
        return None
    return validate_site_modes


def _check_modes_file(rel: str, raw: Any, dep: Deployment, problems: list[str], notes: list[str], validate,
                      room: str | None = None) -> None:
    """A modes file through room_service's own model, as room_service would load it: errors are problems, warnings
    about the screens are notes. ``room``: an own_modes room's file, checked in that room's scope (validate_site_modes'
    ``scope_room``); the site's file is checked in the site's scope when there are zones."""
    kw: dict[str, Any] = {}
    if room is not None:
        import inspect  # noqa: PLC0415
        if "scope_room" in inspect.signature(validate).parameters:
            kw["scope_room"] = room
    for item in validate(raw, _or_empty(rooms, dep), _or_empty(destinations, dep), warnings=True, **kw):
        where = f"{rel}{item.get('path') or ''}: {item.get('message')}"
        if item.get("level") != "warning":
            problems.append(where)
        elif _screen_warning(item):     # only the screens are modelled here; devices are placeholders
            notes.append(where)


def _claims(r: dict) -> tuple[set[str], set[str], set[str]]:
    """A room's (screens, blinds, targets): its lights entries and its effects' power and setting targets. A target
    with no dot ("light") is a whole subsystem."""
    targets = set(_as_list(r.get("lights")) or [])
    for e in r.get("effects") if isinstance(r.get("effects"), list) else []:
        if isinstance(e, str):
            targets.add(e)
        elif isinstance(e, dict):
            if isinstance(e.get("power"), str):
                targets.add(e["power"])
            targets |= set(_as_list(e.get("settings")) or [])
    return set(_as_list(r.get("screens")) or []), set(_as_list(r.get("blinds")) or []), targets


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _check_rooms(dep: Deployment, rms: dict, caps: dict, problems: list[str], notes: list[str]) -> None:
    """The optional per-room keys "own_modes" and "show" (deployments/README.md, "Rooms with their own modes")."""
    for rid, r in rms.items():
        for key in ("own_modes", "show"):
            if key in r and not isinstance(r[key], bool):
                problems.append(f"rooms.json: room {rid!r}: \"{key}\" must be true or false, not {r[key]!r}")
    dr = _or_empty(default_room, dep)
    if dr in rms:
        if rms[dr].get("own_modes") is True:
            problems.append(f"rooms.json: room {dr!r} is the default room (the show, Auto and the art director serve "
                            "it, with the site's modes), so it cannot have \"own_modes\": true")
        if rms[dr].get("show") is False:
            problems.append(f"rooms.json: room {dr!r} is the default room (the room the show plays in), so it cannot "
                            "have \"show\": false")
    zones = [rid for rid, r in rms.items() if r.get("own_modes") is True]
    claims = {rid: _claims(r) for rid, r in rms.items()}
    for z in zones:
        if not _ROOM_FILE_ID.match(z):
            problems.append(f"rooms.json: room {z!r} has \"own_modes\": true but its id can't name a file "
                            "(letters, digits, - and _ only)")
            continue
        rel = f"room/modes.{z}.json"
        p = room_modes_path(dep, z)
        if not p.is_file():
            problems.append(f"{rel}: missing (rooms.json room {z!r} has \"own_modes\": true)")
        elif caps.get("room") and (validate := _validator(rel, notes)) is not None:
            try:
                _check_modes_file(rel, read_data(p), dep, problems, notes, validate, room=z)
            except Exception as e:  # noqa: BLE001
                problems.append(f"{rel}: {e}")
        zs, zb, zt = claims[z]
        for whole in sorted(t for t in zt if "." not in t):
            problems.append(f"rooms.json: room {z!r} has its own modes but lists the whole \"{whole}\" subsystem; "
                            f"list its {whole} members by id (\"{whole}.<id>\")")
        for o in rms:
            if o == z or (o in zones and list(rms).index(o) < list(rms).index(z)):   # a zone pair is said once
                continue
            os_, ob, ot = claims[o]
            shared = sorted(zs & os_) + sorted(zb & ob) + sorted(
                (zt & ot) | {t for t in zt if "." in t and t.split(".", 1)[0] in ot})
            if shared:
                problems.append(f"rooms.json: room {z!r} has its own modes but shares {', '.join(shared)} with room "
                                f"{o!r}; whatever two rooms claim stays with the site's modes")
    if caps.get("room"):
        shared_modes = [rid for rid, r in rms.items() if r.get("own_modes") is not True and claims[rid][0]]
        if len(shared_modes) >= 2:
            notes.append(f"rooms.json: {_and(shared_modes)} share the site's modes; give one \"own_modes\": true to "
                         "switch it alone")
    for rid, r in rms.items():
        if r.get("show") is False and _as_list(r.get("lights")):
            notes.append(f"rooms.json: room {rid!r} is outside the show (\"show\": false) but lists lights: its "
                         "lights still play the show's look")


def _old_path_notes(dep: Deployment, notes: list[str]) -> None:
    """A file still at a pre-deployments path (smconfig/legacy.py): nothing reads it, so a session or tool that follows
    an old note and edits it changes nothing. A note, never a problem: it doesn't stop anything."""
    from . import REPO_ROOT    # read at call time (tests point it elsewhere)
    from .legacy import leftovers

    found = leftovers(REPO_ROOT)
    if found:
        files = "; ".join(f"{old} (now deployments/{dep.id}/{new})" for old, new in found)
        notes.append(f"files at old paths: {files}. Nothing reads them any more, so an edit there never takes "
                     f"effect. `.venv/bin/python scripts/migrate_deployment.py --deployment {dep.id}` lists them, and "
                     "--apply moves each one (or removes it when the deployment's copy is the same)")


def _frontend_notes(dep: Deployment, caps: dict, notes: list[str]) -> None:
    """The frontend's build-time addresses (FRONTEND_ADDRESSES) are in .env and use the manifest's ports. Notes only.
    Skipped when this machine's .env names another deployment."""
    from urllib.parse import urlsplit

    from . import env as _env

    selected = _env("SM_DEPLOYMENT")
    if selected and selected != dep.id:
        return
    missing = []
    for name, (svc, cap) in FRONTEND_ADDRESSES.items():
        if cap and not caps.get(cap):
            continue
        value = _env(name)
        if not value:
            missing.append(name)
            continue
        try:
            got = urlsplit(value).port
        except ValueError:
            notes.append(f".env: {name} has a port that isn't a number; the app is built with it (npx vite build)")
            continue
        want = dep.ports[svc]
        if got is not None and got != want:
            notes.append(f".env: {name} uses port {got}, but deployment.yaml's ports.{svc} is {want}. The app is built "
                         f"with it (npx vite build): make them agree and rebuild, unless something forwards {got} to "
                         f"{want}")
    if missing:
        it = "it" if len(missing) == 1 else "them"
        notes.append(f".env lacks {_and(missing)}. The app is built with {it} (npx vite build), and without {it} the "
                     "TV pages and the Room page can't reach this machine's services. deployments/README.md › "
                     f"\"Setting up a new deployment\" lists {it}; rebuild after setting {it}")


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
    if caps.get("room") and modes.is_file() and (validate := _validator("room/modes.json", notes)) is not None:
        try:
            modes_raw = read_data(modes)
            _check_modes_file("room/modes.json", modes_raw, dep, problems, notes, validate)
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
    _check_rooms(dep, rms, caps, problems, notes)
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
    _frontend_notes(dep, caps, notes)
    _old_path_notes(dep, notes)
    for cap in ("generation", "alexa", "tv_power", "kiosk_admin"):
        if not caps.get(cap):
            notes.append(f"{cap}: off")
    return problems, notes
