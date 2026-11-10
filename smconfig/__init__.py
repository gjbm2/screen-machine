"""smconfig: the one place every screen-machine process learns which deployment it is part of.

A *deployment* is one installation of the app on one machine: PC22's drawing room ("finsbury"), a laptop rig, a
test box. Everything that differs between deployments lives in one directory, ``deployments/<id>/``:

    deployment.yaml          the manifest: name, location, capabilities, ports, default room, kiosk, host paths
    publish-destinations.json  the screens and publish targets (Flask, room_service, stream_service read it)
    rooms.json               which room holds which screens, lights, blinds, sensors, effects
    intensity.json           light sensor -> screens group, and the lux curve (Flask's lightsensor)
    show.yaml                this site's layer over config/room/show.yaml (deep-merged; senders, room facts)
    dot.json                 the e-ink tags (dot_service)
    room/                    room_service: room.yaml, modes.json, auto_hints.json, sonos.json, tv.json, rf/*.json
    lights/                  the rig: patch.yaml, fixtures*.yaml, space*.yaml
    calibration/             latency.json, audio_cal.json
    live/                    stream_switches.json (per-screen encoder switches)
    services/                sm-stream.env, sm-show.env, sm-dot.env (scripts/ctl's per-service flags)

Shared behaviour (scenes, effects, vibes, the tuning part of show.yaml, LLM routing, prompts, refiners) stays in
``config/`` and ``routes/data/`` and is the same for every deployment. Secrets live in ``~/.config/screen-machine/``
and runtime state in ``~/.local/state/screen-machine/``, both per machine, as before.

The deployment is chosen by ``SM_DEPLOYMENT`` (the process environment first, else the repo's ``.env``). There is
no default: a process that cannot tell which deployment it belongs to stops with a clear message rather than
guessing, because a wrong guess would drive another site's lights and screens.

This module is deliberately stdlib + PyYAML only, so every venv (Flask, room, show, stream, dot, piano) can import
it, and scripts/ctl and start.sh can call ``python -m smconfig get ...``.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENTS_DIR = REPO_ROOT / "deployments"
CONFIG_DIR = REPO_ROOT / "config"
ENV_FILE = REPO_ROOT / ".env"
MANIFEST = "deployment.yaml"

SECRETS_DIR = Path("~/.config/screen-machine").expanduser()
STATE_DIR = Path("~/.local/state/screen-machine").expanduser()

# Every capability the code knows. A deployment's manifest switches each one on or off; one it leaves out is off.
# The comment says what the switch gates, so `smconfig check` and the README can explain it.
CAPABILITIES: dict[str, str] = {
    "generation": "AI image/video generation (RunPod + LLM refiners): /api/generate-image, scheduler generate/animate, Alexa generation",
    "art_director": "Flask's art director: makes new pictures for thin screen libraries (needs generation)",
    "art_tags": "Flask's AI tagging of bucket files (LLM)",
    "alexa": "the /api/alexa skill endpoint",
    "tv_power": "Samsung Frame control: wake (WoL), sleep, standby, art-mode sync (scheduler device_* and Alexa)",
    "kiosk_admin": "the admin API's kiosk actions over SSH (restart kiosk/screen, set URL, reboot)",
    "alerts": "Siren alert e-mails and the healthcheck ping",
    "ngrok": "start.sh's ngrok tunnel (Alexa webhook)",
    "room": "room_service inside the Flask process (:8700)",
    "live": "stream_service, the GPU renderer, and live sessions on screens",
    "show": "show_service: the room's ears, the director and the light rig",
    "dot": "dot_service: the e-ink tags",
    "dmx": "room_service's DMX stack: Art-Net in, the show bus :8701 and the Hue patch (lights/patch.yaml)",
    "hue": "Philips Hue lamps (pairing in ~/.config/screen-machine/hue/)",
    "rf": "Broadlink RM4 Pro RF devices (room/rf/*.json): glitterball, pinspots",
    "blinds": "the Bloc iQ blinds hub (room/rf/blinds.json)",
    "sonos": "Sonos zones (room/sonos.json)",
}

# Ports of the services on this machine (loopback unless a service says otherwise). A manifest may override any.
DEFAULT_PORTS: dict[str, int] = {
    "flask": 5000,       # the app: API, SPA, /output/, /display/*
    "overlay_ws": 8765,  # Flask's overlay and light-relay WebSocket
    "room": 8700,        # room_service's LAN door (inside Flask)
    "show_bus": 8701,    # room_service's show bus (loopback)
    "stream": 8702,      # stream_service (GPU renderer)
    "show": 8703,        # show_service bus + API (loopback)
    "dot": 8704,         # dot_service (loopback)
}


class DeploymentError(RuntimeError):
    """The deployment is not selected, missing, or its manifest is malformed."""


# --------------------------------------------------------------------------------------------------------------------
# Settings from the environment, else the repo's .env (owner, 14 Oct: machine addresses live in .env only)
# --------------------------------------------------------------------------------------------------------------------

def _read_env_file(path: Path | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = (path or ENV_FILE).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, sep, v = line.partition("=")
        if not sep:
            continue
        v = v.strip()
        if v[:1] in ("'", '"') and v[-1:] == v[:1] and len(v) >= 2:
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].split("\t#", 1)[0].strip()
            if v.startswith("#"):
                v = ""
        out[k.strip()] = v
    return out


def env(name: str, default: str = "") -> str:
    """A machine setting: the process environment first, else the repo's .env, else ``default``."""
    v = os.environ.get(name)
    if v is not None and v.strip() != "":
        return v.strip()
    return _read_env_file().get(name, default) or default


# --------------------------------------------------------------------------------------------------------------------
# YAML (PyYAML is in every venv; imported lazily so `python -m smconfig get` stays cheap)
# --------------------------------------------------------------------------------------------------------------------

def _yaml():
    import yaml  # noqa: PLC0415
    return yaml


def read_data(path: Path) -> Any:
    """A JSON or YAML file (by extension) parsed; raises OSError / ValueError."""
    text = Path(path).read_text()
    if Path(path).suffix in (".yaml", ".yml"):
        return _yaml().safe_load(text)
    return json.loads(text)


def deep_merge(base: Any, overlay: Any) -> Any:
    """``overlay`` merged over ``base``: mappings merge key by key, recursively; anything else in the overlay
    (a list, a scalar, null) replaces the base value whole. Neither input is modified."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = {k: copy.deepcopy(v) for k, v in base.items()}
        for k, v in overlay.items():
            out[k] = deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(overlay)


def load_layered_yaml(base: Path, overlay: Path | None) -> dict:
    """The shared file ``base`` with the deployment's ``overlay`` deep-merged over it (an absent overlay is no
    layer). Both must be YAML mappings (an empty file counts as {})."""
    yaml = _yaml()
    b = yaml.safe_load(Path(base).read_text()) or {}
    if not isinstance(b, dict):
        raise ValueError(f"{base}: top level must be a mapping")
    if overlay is None or not Path(overlay).exists():
        return b
    o = yaml.safe_load(Path(overlay).read_text()) or {}
    if not isinstance(o, dict):
        raise ValueError(f"{overlay}: top level must be a mapping")
    return deep_merge(b, o)


# --------------------------------------------------------------------------------------------------------------------
# The deployment
# --------------------------------------------------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def available() -> list[str]:
    """The deployments in this checkout (directories under deployments/ with a manifest)."""
    if not DEPLOYMENTS_DIR.is_dir():
        return []
    return sorted(p.name for p in DEPLOYMENTS_DIR.iterdir() if (p / MANIFEST).is_file())


def deployment_id() -> str:
    """The selected deployment's id (SM_DEPLOYMENT). Raises DeploymentError if unset or unknown."""
    dep = env("SM_DEPLOYMENT")
    if not dep:
        raise DeploymentError(
            "SM_DEPLOYMENT is not set. Add SM_DEPLOYMENT=<id> to this machine's .env (or the environment); "
            f"deployments in this checkout: {', '.join(available()) or 'none'}. See deployments/README.md.")
    if not _ID_RE.match(dep):
        raise DeploymentError(f"SM_DEPLOYMENT={dep!r} is not a valid deployment id")
    if not (DEPLOYMENTS_DIR / dep / MANIFEST).is_file():
        raise DeploymentError(
            f"SM_DEPLOYMENT={dep!r} but {DEPLOYMENTS_DIR / dep / MANIFEST} does not exist; "
            f"deployments in this checkout: {', '.join(available()) or 'none'}")
    return dep


@dataclass
class Deployment:
    """One deployment's manifest, resolved. Paths are absolute."""

    id: str
    dir: Path
    manifest: dict[str, Any] = field(repr=False)

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.id)

    @property
    def capabilities(self) -> dict[str, bool]:
        caps = self.manifest.get("capabilities") or {}
        return {k: bool(caps.get(k, False)) for k in CAPABILITIES}

    def enabled(self, capability: str) -> bool:
        if capability not in CAPABILITIES:
            raise KeyError(f"unknown capability {capability!r} (known: {', '.join(CAPABILITIES)})")
        return bool((self.manifest.get("capabilities") or {}).get(capability, False))

    @property
    def ports(self) -> dict[str, int]:
        return {**DEFAULT_PORTS, **{k: int(v) for k, v in (self.manifest.get("ports") or {}).items()}}

    def port(self, service: str) -> int:
        try:
            return self.ports[service]
        except KeyError:
            raise KeyError(f"unknown service {service!r} (known: {', '.join(self.ports)})") from None

    def url(self, service: str, scheme: str = "http", host: str = "127.0.0.1") -> str:
        """The loopback URL one process on this machine uses to reach another (no trailing slash)."""
        return f"{scheme}://{host}:{self.port(service)}"

    @property
    def location(self) -> dict[str, float]:
        loc = self.manifest.get("location") or {}
        return {"lat": float(loc["lat"]), "lon": float(loc["lon"])} if "lat" in loc and "lon" in loc else {}

    @property
    def default_room(self) -> str | None:
        r = self.manifest.get("default_room")
        return str(r) if r else None

    @property
    def kiosk(self) -> dict[str, Any]:
        return dict(self.manifest.get("kiosk") or {})

    @property
    def host(self) -> dict[str, Any]:
        """Machine-specific paths and tools named by the manifest (fonts, Windows interop, venvs)."""
        return dict(self.manifest.get("host") or {})

    def host_path(self, key: str, default: str | None = None) -> Path | None:
        v = self.host.get(key, default)
        return Path(str(v)).expanduser() if v else None

    def path(self, *parts: str) -> Path:
        """A file or directory of this deployment (it need not exist yet: the app writes some of them)."""
        p = self.dir.joinpath(*parts)
        try:
            p.resolve().relative_to(self.dir.resolve())
        except ValueError:
            raise DeploymentError(f"{'/'.join(parts)!r} leaves the deployment directory") from None
        return p

    def section(self, key: str) -> dict[str, Any]:
        v = self.manifest.get(key)
        return dict(v) if isinstance(v, dict) else {}


_lock = threading.Lock()
_cache: dict[str, tuple[float, Deployment]] = {}


def load(dep_id: str | None = None) -> Deployment:
    """The deployment ``dep_id`` (default: the selected one). The manifest is re-read when it changes on disk."""
    dep_id = dep_id or deployment_id()
    d = DEPLOYMENTS_DIR / dep_id
    mf = d / MANIFEST
    try:
        mtime = mf.stat().st_mtime_ns
    except OSError as e:
        raise DeploymentError(f"{mf}: {e}") from e
    with _lock:
        hit = _cache.get(dep_id)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        manifest = _yaml().safe_load(mf.read_text()) or {}
    except Exception as e:  # noqa: BLE001 - surfaced with the file name
        raise DeploymentError(f"{mf}: {e}") from e
    if not isinstance(manifest, dict):
        raise DeploymentError(f"{mf}: top level must be a mapping")
    if manifest.get("id", dep_id) != dep_id:
        raise DeploymentError(f"{mf}: id {manifest.get('id')!r} does not match its directory {dep_id!r}")
    dep = Deployment(id=dep_id, dir=d, manifest=manifest)
    with _lock:
        _cache[dep_id] = (mtime, dep)
    return dep


def deployment() -> Deployment:
    """The selected deployment."""
    return load()


# Short forms for the common calls ------------------------------------------------------------------------------------

def path(*parts: str) -> Path:
    """A file of the selected deployment, e.g. ``smconfig.path("lights", "patch.yaml")``."""
    return deployment().path(*parts)


def shared(*parts: str) -> Path:
    """A shared behaviour file under config/, the same for every deployment."""
    return CONFIG_DIR.joinpath(*parts)


def secret(*parts: str) -> Path:
    """A secret of this machine under ~/.config/screen-machine/ (never in git)."""
    return SECRETS_DIR.joinpath(*parts)


def state(*parts: str) -> Path:
    """Runtime state of this machine under ~/.local/state/screen-machine/."""
    return STATE_DIR.joinpath(*parts)


def enabled(capability: str) -> bool:
    return deployment().enabled(capability)


def port(service: str) -> int:
    return deployment().port(service)


def url(service: str, scheme: str = "http") -> str:
    return deployment().url(service, scheme)


# --------------------------------------------------------------------------------------------------------------------
# The site entities everyone joins on: screens and rooms
# --------------------------------------------------------------------------------------------------------------------

def destinations(dep: Deployment | None = None) -> list[dict[str, Any]]:
    """publish-destinations.json: every publish target, physical screens included."""
    dep = dep or deployment()
    data = read_data(dep.path("publish-destinations.json"))
    if not isinstance(data, list):
        raise ValueError(f"{dep.path('publish-destinations.json')}: expected a list")
    return [d for d in data if isinstance(d, dict) and d.get("id")]


def live_screens(dep: Deployment | None = None) -> list[str]:
    """Ids of the screens that can go live (a destination with a ``live`` block), in file order."""
    return [d["id"] for d in destinations(dep) if isinstance(d.get("live"), dict)]


def rooms(dep: Deployment | None = None) -> dict[str, dict[str, Any]]:
    """rooms.json without its comment keys."""
    dep = dep or deployment()
    data = read_data(dep.path("rooms.json"))
    return {k: v for k, v in (data or {}).items() if not k.startswith("_") and isinstance(v, dict)}


def default_room(dep: Deployment | None = None) -> str | None:
    """The manifest's default_room, else the first room in rooms.json."""
    dep = dep or deployment()
    return dep.default_room or next(iter(rooms(dep)), None)


__all__ = [
    "CAPABILITIES", "CONFIG_DIR", "DEFAULT_PORTS", "DEPLOYMENTS_DIR", "Deployment", "DeploymentError", "REPO_ROOT",
    "SECRETS_DIR", "STATE_DIR", "available", "deep_merge", "default_room", "deployment", "deployment_id",
    "destinations", "enabled", "env", "live_screens", "load", "load_layered_yaml", "path", "port", "read_data",
    "rooms", "secret", "shared", "state", "url",
]
