"""room_service configuration: config/room/room.prod.yaml (LIVE_V1 §3.1) plus the site files.

Screens are defined once, in routes/data/publish-destinations.json (Flask's display config): every
destination with a "live" block is a room screen, with its name, size (maxwidth x maxheight) and live
settings ({"enabled", "gpu", "profile", "mask_push", "timed_reload"?}). A `screens:` block in the YAML
overrides per screen and per field, and may add screens (tests, dev configs).

The extra sections below (`timeouts`, `snapshot`, `service`) are additive and optional; their defaults
are the values in AV_PROGRAMME §3.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import os
import re
from pathlib import Path
from typing import Any

import json
import logging

import yaml

from .ingest_config import IngestConfig, ingest_config_from_dict

log = logging.getLogger("room_service.config")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "room" / "room.prod.yaml"
DEFAULT_DESTINATIONS = REPO_ROOT / "routes" / "data" / "publish-destinations.json"
DEFAULT_STATE_DIR = Path("~/.local/state/screen-machine/room")
DEFAULT_TOKEN_FILE = Path("~/.config/screen-machine/room_service.token")
DEFAULT_CHROME = "~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"
# LIVE_V1 §3 "Browser protection": the Host header must name one of these (port ignored),
# which defends against DNS rebinding.
def _env(name: str) -> str:
    """A setting from the environment, else from the repo's .env (owner, 14 Oct: addresses live in .env only)."""
    if os.environ.get(name):
        return os.environ[name].strip()
    try:
        for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == name:
                return v.split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _origin(url: str) -> str:
    m = re.match(r"^(https?://[^/\s]+)", url or "")
    return m.group(1) if m else ""


LAN_HOST = _env("SM_LAN_HOST")                    # e.g. 192.168.1.205 (.env)
APP_ORIGIN = _origin(_env("VITE_URL"))            # the app's public origin (.env VITE_URL); the kiosk pages load from it
APP_LAN_ORIGIN = _origin(_env("VITE_APP_LAN_URL"))  # the app on the LAN, or its friendly HTTPS name (Caddy /rs -> here)
APP_LAN_HOST = re.sub(r"^https?://|:\d+$", "", APP_LAN_ORIGIN)
DEFAULT_ALLOWED_HOSTS = [h for h in dict.fromkeys((LAN_HOST, APP_LAN_HOST, "localhost", "127.0.0.1", "::1")) if h]
DEFAULT_PAGE_KEYS_FILE = Path("~/.config/screen-machine/room_page_keys.json")
DEFAULT_WEB_ORIGINS = [o for o in (
    f"http://{LAN_HOST}:8080" if LAN_HOST else "", f"http://{LAN_HOST}:5000" if LAN_HOST else "",
    APP_ORIGIN, APP_LAN_ORIGIN,
    "http://localhost:5000", "http://127.0.0.1:5000", "http://localhost:8080",
    "http://localhost:5174", "http://127.0.0.1:5174", "http://localhost:5185", "http://127.0.0.1:5185",
) if o]


@dataclass
class ScreenConfig:
    id: str
    gpu: int = 0
    live_enabled: bool = False
    mask_push: bool = False
    timed_reload: bool = True         # LIVE_V1 §3.1: the page policy's default; modes can change it
    profile: str = "hevc-2160p60"     # used when a live spec names no profile
    name: str = ""                    # the destination's name ("North screen")
    width: int | None = None          # the display's size (maxwidth x maxheight): render and snapshot size
    height: int | None = None
    auto: dict | None = None          # publish-destinations live.auto: the director block every new session gets

    @property
    def label(self) -> str:
        return self.name or self.id


@dataclass
class MaskConfig:
    deadband_brightness: float = 0.005
    deadband_warm_alpha: float = 0.002
    transition_ms: int = 200
    backstop_s: float = 1.0
    # LIVE_V1 §2.2b "Interim": at most one GET /api/<id>/mask per second per screen. Each GET
    # advances Flask's shared, read-driven intensity EMA, so polling faster would also speed
    # up the production TVs' masks. Don't lower this outside tests.
    min_fetch_interval_s: float = 1.0
    max_requests_per_s: float = 10.0  # total GET /api/<id>/mask budget across screens
    fetch_timeout_s: float = 2.0
    enabled: bool = True


@dataclass
class Timeouts:
    """Handshake timeouts (AV_PROGRAMME §3.3) and supervision timings."""
    freeze_ack_s: float = 5.0          # freeze -> ack frozen (page settle cap 4.5 s: a picture
                                       # change through black takes >= 3.1 s; DCL v3 §3.6)
    snapshot_s: float = 3.0            # headless screenshot
    session_ready_s: float = 7.0       # POST session -> session.ready (a render worker's cold start counts here)
    live_visible_s: float = 10.0       # live.attach -> ack live_visible (page gives up at 8 s)
    held_extra_s: float = 5.0          # exit -> session.held: out transition + this
    art_visible_extra_s: float = 10.0  # live.leave -> ack art_visible: out transition + this
    entering_extra_s: float = 5.0      # go -> 'live' phase if no "in done" event: in transition + this
    abort_ack_s: float = 1.0           # live.abort -> art_visible (best effort, not required)
    backoff_s: list[float] = field(default_factory=lambda: [2.0, 5.0, 15.0, 30.0])
    fallback_window_s: float = 600.0
    fallback_limit: int = 3            # this many fallbacks inside the window -> phase failed
    max_entry_failures: int = 3        # consecutive failed entry handshakes -> phase failed
    stable_reset_s: float = 60.0       # live this long -> the retry backoff starts again at the first step
    adopt_wait_s: float = 20.0         # after a restart: wait this long for the page to say what it shows
    page_hb_idle_s: float = 2.0        # send hb when a page socket has been idle this long
    page_dead_s: float = 15.0          # LIVE_V1 §2.2a.5: a client silent this long is dropped
    page_hello_s: float = 10.0         # a page must send hello within this
    page_behind_s: float = 5.0         # LIVE_V1 §2.2a.1: oldest unsent message older than this -> drop
    page_behind_bytes: int = 256 * 1024  # LIVE_V1 §2.2a.1: more than this unsent -> drop
    create_session_s: float = 3.0      # POST /api/sessions HTTP timeout, inside the freeze
    exit_session_s: float = 5.0        # POST /api/sessions/{id}/exit HTTP timeout
    settle_before_go_s: float = 1.0    # after ack live_visible, before go: the browser's decoder warms up on the
                                       # held anchor (nothing visible changes), so the in-fade starts smoothly
    go_s: float = 2.0                  # POST go HTTP timeout, inside the freeze. Entry worst case:
                                       # 5 + 3 + 3 + 7 + 8 (page's live_failed) + 1 (settle) + 2 = 29 s,
                                       # under the page's 30 s freeze safety
    policy_ack_s: float = 10.0         # page policy: wait this long for kiosk.state to report it
    sweep_delay_s: float = 2.0         # after a create that failed client-side: sweep orphans this soon
    step_timeout_s: float = 120.0      # coordinator: how long a screen step may take to settle
    after_timeout_s: float = 120.0     # coordinator: how long a sequence `after:` may wait
    http_s: float = 10.0               # REST calls to stream_service (session create can be ~20 MB)
    default_transition_s: float = 3.0  # transition length when the catalog cannot say


@dataclass
class SnapshotConfig:
    enabled: bool = True
    executable_path: str = DEFAULT_CHROME
    viewport_w: int = 3840
    viewport_h: int = 2160
    timeout_s: float = 3.0             # per snapshot (render + screenshot)
    page_load_timeout_s: float = 20.0  # (re)creating a page, outside a handshake
    warm: bool = True                  # open pages for live-enabled screens at startup
    ffmpeg: str = "ffmpeg"
    clip_base: str | None = None       # resolves relative clip URLs; default: flask.url
    headless_args: list[str] = field(default_factory=lambda: [
        "--force-color-profile=srgb", "--hide-scrollbars", "--mute-audio",
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
    ])


@dataclass
class RoomConfig:
    stream_url: str = "http://127.0.0.1:8082"
    show_url: str = "http://127.0.0.1:8703"   # show_service (c8): its API and card, passed through at /show/*
    stream_token_file: Path = Path("~/.config/screen-machine/stream_service.token")   # sent if present (no auth now)
    flask_url: str = "http://127.0.0.1:5000"
    flask_ws: str = "ws://127.0.0.1:8765/"
    art_page_base: str = "http://127.0.0.1:5174"
    mask: MaskConfig = field(default_factory=MaskConfig)
    screens: dict[str, ScreenConfig] = field(default_factory=dict)
    web_origins: list[str] = field(default_factory=lambda: list(DEFAULT_WEB_ORIGINS))
    allowed_hosts: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS))
    # Browser origins allowed on the page control socket besides web_origins and art_page_base's
    # own (DCL v3 §7). The TVs load the page from their kiosk URL's origin, so that origin must
    # be listed here before `?ctl=` goes on a TV.
    kiosk_origins: list[str] = field(default_factory=list)
    timeouts: Timeouts = field(default_factory=Timeouts)
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)
    modes_file: Path = REPO_ROOT / "config" / "room" / "modes.json"   # ROOM_STATE_MODEL §2 (JSON; YAML also loads)
    state_dir: Path = DEFAULT_STATE_DIR
    token_file: Path = DEFAULT_TOKEN_FILE
    # Per-screen keys for the kiosk control socket (LIVE_V1 §3 "Machine channels"): a JSON
    # object {screen: key}, mode 0600. A screen with a key needs it (?ctlkey= or hello.key)
    # for role kiosk|lab; a screen without one accepts unauthenticated glass clients (warned).
    page_keys_file: Path = DEFAULT_PAGE_KEYS_FILE
    sweep_orphans: bool = True        # delete stray stream sessions for our screens (each renew cycle)
    blinds_enabled: bool = True       # `blinds: {enabled: false}`: the hub blinds become a placeholder (tests, no LAN)
    sonos_enabled: bool = True        # `sonos: {enabled: false}`: Sonos becomes a placeholder (tests, no LAN)
    rooms_file: Path = REPO_ROOT / "routes" / "data" / "rooms.json"   # the site: which room holds what
    output_dir: Path = REPO_ROOT / "output"   # Flask's /output/: the art files the pages show (their shape sizes live)
    destinations_file: Path | None = None   # the screens: destinations with a "live" block (default: DEFAULT_DESTINATIONS)
    session_ttl_s: int = 120
    renew_every_s: float = 30.0
    config_path: Path | None = None
    ingest: IngestConfig = field(default_factory=IngestConfig)   # LIVE_V1 §2.2b (ingest_config.py)
    # Virtual DMX devices (room_service/dmx): Art-Net in, the show bus out, Hue streaming. Opt-in:
    # `dmx: {enabled: true, patch_file: config/room/patch.yaml, show_bus_port: 8701}`
    # (0 = not served on :8701; WS /api/v1/dmx/live still is).
    dmx_enabled: bool = False
    patch_file: Path = REPO_ROOT / "config" / "room" / "patch.yaml"
    show_bus_port: int = 8701

    def screen(self, screen_id: str) -> ScreenConfig | None:
        return self.screens.get(screen_id)


def _fill(cls, data: dict | None):
    """Build dataclass `cls` from a dict, ignoring unknown keys (forward compatible)."""
    data = data or {}
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _size(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 16384 else None


def screens_from_destinations(path: Path) -> dict[str, dict]:
    """{id: ScreenConfig fields} for every destination with a "live" block; {} if the file can't be read."""
    try:
        dests = json.loads(Path(path).read_text())
    except FileNotFoundError:
        log.warning("no destinations file %s: screens come only from the YAML", path)
        return {}
    except (OSError, ValueError) as e:
        log.error("destinations file %s not read (%s): screens come only from the YAML", path, e)
        return {}
    out: dict[str, dict] = {}
    for d in dests if isinstance(dests, list) else []:
        live = d.get("live") if isinstance(d, dict) else None
        if not isinstance(live, dict) or not isinstance(d.get("id"), str):
            continue
        sc: dict[str, Any] = {"name": str(d.get("name") or ""), "width": _size(d.get("maxwidth")),
                              "height": _size(d.get("maxheight")), "live_enabled": bool(live.get("enabled", False))}
        for k in ("gpu", "profile", "mask_push", "timed_reload"):
            if k in live:
                sc[k] = live[k]
        if isinstance(live.get("auto"), dict):
            sc["auto"] = live["auto"]
        out[d["id"]] = sc
    return out


def config_from_dict(raw: dict[str, Any], *, base_dir: Path | None = None) -> RoomConfig:
    base_dir = base_dir or REPO_ROOT
    cfg = RoomConfig()
    ss = raw.get("stream_service") or {}
    cfg.stream_url = str(ss.get("url", cfg.stream_url)).rstrip("/")
    cfg.show_url = str((raw.get("show_service") or {}).get("url", cfg.show_url)).rstrip("/")
    cfg.stream_token_file = Path(ss.get("token_file", cfg.stream_token_file)).expanduser()
    fl = raw.get("flask") or {}
    cfg.flask_url = str(fl.get("url", cfg.flask_url)).rstrip("/")
    cfg.flask_ws = str(fl.get("ws", cfg.flask_ws))
    cfg.art_page_base = str(raw.get("art_page_base", cfg.art_page_base)).rstrip("/")
    cfg.mask = _fill(MaskConfig, raw.get("mask"))
    cfg.timeouts = _fill(Timeouts, raw.get("timeouts"))
    cfg.timeouts.backoff_s = [float(x) for x in cfg.timeouts.backoff_s] or [2.0]
    cfg.snapshot = _fill(SnapshotConfig, raw.get("snapshot"))
    cfg.web_origins = list(raw.get("web_origins") or DEFAULT_WEB_ORIGINS)
    cfg.kiosk_origins = [str(o) for o in (raw.get("kiosk_origins") or [])]
    cfg.allowed_hosts = [str(h).lower().strip("[]") for h in (raw.get("allowed_hosts") or DEFAULT_ALLOWED_HOSTS)]
    svc = raw.get("service") or {}

    def _path(v: Any, default: Path) -> Path:
        if v is None:
            return default.expanduser()
        p = Path(str(v)).expanduser()
        return p if p.is_absolute() else (base_dir / p)

    cfg.modes_file = _path(svc.get("modes_file"), cfg.modes_file)
    cfg.rooms_file = _path(svc.get("rooms_file"), cfg.rooms_file)
    cfg.output_dir = _path(svc.get("output_dir"), cfg.output_dir)
    cfg.destinations_file = _path(svc.get("destinations_file"), DEFAULT_DESTINATIONS)   # (looked up now: tests patch it)
    # Screens: the destinations with a "live" block, then the YAML's `screens:` per screen and per field.
    merged = screens_from_destinations(cfg.destinations_file)
    for sid, sc in (raw.get("screens") or {}).items():
        merged[str(sid)] = {**merged.get(str(sid), {}), **(sc or {})}
    for sid, sc in merged.items():
        cfg.screens[sid] = _fill(ScreenConfig, {**sc, "id": sid})
    cfg.state_dir = _path(svc.get("state_dir"), cfg.state_dir)
    cfg.token_file = _path(svc.get("token_file"), cfg.token_file)
    cfg.page_keys_file = _path(svc.get("page_keys_file"), cfg.page_keys_file)
    cfg.sweep_orphans = bool(svc.get("sweep_orphans", cfg.sweep_orphans))
    cfg.blinds_enabled = bool((raw.get("blinds") or {}).get("enabled", True))
    cfg.sonos_enabled = bool((raw.get("sonos") or {}).get("enabled", True))
    cfg.session_ttl_s = int(svc.get("session_ttl_s", cfg.session_ttl_s))
    cfg.renew_every_s = float(svc.get("renew_every_s", cfg.renew_every_s))
    cfg.ingest = ingest_config_from_dict(raw, base_dir=base_dir)
    dmx = raw.get("dmx") or {}
    cfg.dmx_enabled = bool(dmx.get("enabled", False))
    cfg.patch_file = _path(dmx.get("patch_file"), cfg.patch_file)
    cfg.show_bus_port = int(dmx.get("show_bus_port", cfg.show_bus_port))
    return cfg


def load_config(path: Path | str | None = None) -> RoomConfig:
    path = Path(path).expanduser() if path else DEFAULT_CONFIG
    if not path.is_absolute() and not path.exists():
        path = REPO_ROOT / path
    path = path.resolve()
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    cfg = config_from_dict(raw, base_dir=REPO_ROOT)
    if "service" not in raw or "modes_file" not in (raw.get("service") or {}):
        cfg.modes_file = path.parent / "modes.json"
    cfg.config_path = path
    return cfg
