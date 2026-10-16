"""show_service settings: CLI args over env over defaults, into ``contract.Config`` (fields only, in contract.py).

```
env (deploy/sm-show.env; scripts/ctl passes the other KEY=VALUE lines as env, and SHOW_ARGS as argv):
  SHOW_SENDERS       "kiosk=screen-machine-drawingroom/room-mic,room-piano;pc22=pc22.lan:6980/pc22-loop"
  SHOW_VBAN_BIND     0.0.0.0:6980            SHOW_BUS_BIND   127.0.0.1:8703
  SHOW_LATENCY_FILE  config/room/latency.json                  SHOW_LOG_LEVEL  INFO
  SHOW_ARTNET_TARGET host:port | off (artnet_out.py; its other SHOW_* keys are read by artnet_out.config_from_env)
  SHOW_STREAM_PRIORITY a,b,c (audio streams ranked first, best first)
  SHOW_BRAIN         on (default) | off: Stage 3's brain (director, Auto, the API's routes, test sources, listener)
  SHOW_FILE          config/room/show.yaml       SHOW_VIBES_FILE  config/room/vibes.yaml
  SHOW_STATE_DIR     ~/.local/state/screen-machine/show (state.json, the jsonl logs, the test children's logs)
args (override env; scripts/ctl always passes --port from SHOW_PORT):
  --sender SPEC (repeatable; any --sender replaces SHOW_SENDERS)   --vban-bind HOST:PORT   --bus-bind HOST:PORT
  --host HOST --port PORT (the bus, over --bus-bind)   --latency-file PATH   --log-level LEVEL
  --artnet-target HOST[:PORT]|off (over SHOW_ARTNET_TARGET; default off)   --stream-priority a,b,c
  --no-brain | --brain (over SHOW_BRAIN)   --show-file PATH   --vibes-file PATH   --state-dir PATH
```

* **Sender spec:** ``NAME=HOST[:PORT][/STREAM,STREAM…]``. ``HOST`` is a DNS name in committed config (no literal
  IPs as defaults anywhere here); the port defaults to 6980; no streams means every ``contract.KNOWN_STREAMS`` name
  (a sender sends the ones it has). Stream names are 1-16 characters (VBAN).
* **Degrade, never refuse:** a bad value (a malformed sender, bind, level or an unknown flag) is skipped or set to
  its default and reported in ``Options.notes``; ``load()`` logs them. ``parse_sender`` and ``parse_bind`` raise
  ValueError for callers that want to know.
* Relative paths resolve against the repo root (scripts/ctl runs from there anyway).
* ``acoustic_offsets(path)``: ``sources.<stream>.acoustic_offset_ms`` from latency.json (v2, 10's schema; read-only
  for us), finite numbers only; ``{}`` if absent; never raises. ``LatencyWatch`` re-reads it when its mtime or size
  changes (the main thread polls every 2 s), and keeps the last offsets over a torn or invalid file.
* ``Options.artnet_target`` is the Art-Net out target, ``--artnet-target`` over ``SHOW_ARTNET_TARGET`` (``host``
  alone means port 6454): None (off) by default, so nothing is sent unless the env file or a flag says so.
  ``__main__`` passes it to artnet_out.VirtualMaestro over ``config_from_env()``'s.
* ``--stream-priority`` (or ``SHOW_STREAM_PRIORITY``) fills ``Config.stream_priority``: the receiver ranks those
  audio streams first, in that order, then ``contract.AUDIO_PRIORITY`` (``contract.stream_rank``).
* **The brain** (README "Stage 3", S3.4): ``load_options`` turns it on unless ``--no-brain`` or ``SHOW_BRAIN=off``
  (the rollback switch: the process is then exactly as before Stage 3). An ``Options`` built in code (tests) has it
  off unless it says ``brain=True``, so no test reaches the real show.yaml, room or state directory by accident.
  ``show_file``, ``vibes_file`` and ``state_dir`` default to contract's paths.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from . import contract
from .contract import Config, Sender

log = logging.getLogger("show_service.config")

DEFAULT_SENDER_PORT = 6980
ARTNET_PORT = 6454                # Art-Net's UDP port, when a target names only a host
STREAM_NAME_MAX = 16              # vban.STREAM_NAME_MAX (16-byte VBAN stream name)
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_HOST = re.compile(r"^[A-Za-z0-9_.-]{1,253}$")


@dataclass(frozen=True)
class Options:
    """What load_options returns: the Config, the flags Config has no field for, and what was wrong."""
    config: Config
    stream_priority: Optional[tuple[str, ...]] = None    # also config.stream_priority (kept for callers)
    artnet_target: Optional[tuple[str, int]] = None      # None = off
    notes: tuple[str, ...] = ()
    brain: bool = False                                  # load_options: on unless --no-brain / SHOW_BRAIN=off
    show_file: pathlib.Path = contract.SHOW_FILE
    vibes_file: pathlib.Path = contract.VIBES_FILE
    state_dir: pathlib.Path = contract.STATE_DIR


# -- parsers ----------------------------------------------------------------------------------------------------------
def _port(text: str) -> int:
    try:
        p = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"bad port {text!r}") from None
    if not 0 <= p <= 65535:
        raise ValueError(f"port {p} out of range")
    return p


def parse_bind(text: str, default: tuple[str, int]) -> tuple[str, int]:
    """``host:port``, ``:port`` or ``host`` (the missing half from ``default``)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty bind")
    host, sep, port = text.rpartition(":")
    if not sep:
        host, port = text, ""
    host = host.strip() or default[0]
    if not _HOST.match(host):
        raise ValueError(f"bad host {host!r}")
    return host, (_port(port) if port.strip() else default[1])


def parse_sender(spec: str) -> Sender:
    """``kiosk=screen-machine-drawingroom[:6980][/room-mic,room-piano]`` -> contract.Sender (ValueError if bad).
    Without ``NAME=`` the host is the name; without streams it asks for every contract.KNOWN_STREAMS name."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("empty sender spec")
    name, eq, rest = spec.partition("=")
    if not eq:
        name, rest = "", spec
    elif not name.strip():
        raise ValueError(f"empty sender name in {spec!r}")
    addr, slash, streams_text = rest.partition("/")
    host, colon, port_text = addr.strip().partition(":")
    host = host.strip()
    name = name.strip() or host
    if not _NAME.match(name):
        raise ValueError(f"bad sender name {name!r} in {spec!r}")
    if not _HOST.match(host):
        raise ValueError(f"bad sender host {host!r} in {spec!r}")
    port = _port(port_text.strip()) if colon else DEFAULT_SENDER_PORT
    if port == 0:
        raise ValueError(f"sender port 0 in {spec!r}")
    if slash:
        streams = tuple(s.strip() for s in streams_text.split(",") if s.strip())
        if not streams:
            raise ValueError(f"no streams after '/' in {spec!r}")
    else:
        streams = contract.KNOWN_STREAMS
    for s in streams:
        if len(s) > STREAM_NAME_MAX or not s.isprintable():
            raise ValueError(f"bad stream name {s!r} in {spec!r} (1-{STREAM_NAME_MAX} characters)")
    return Sender(name=name, host=host, port=port, streams=tuple(dict.fromkeys(streams)))


def _split_senders(text: str) -> list[str]:
    return [s for s in re.split(r"[;\n]+", text or "") if s.strip()]


def _path(text: str) -> pathlib.Path:
    p = pathlib.Path(text.strip()).expanduser()
    return p if p.is_absolute() else contract.REPO_ROOT / p


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m show_service", description="show_service: the room's own ears",
                                 add_help=True, allow_abbrev=False)
    ap.add_argument("--sender", action="append", default=None, metavar="SPEC",
                    help="NAME=HOST[:PORT][/STREAM,...] (repeatable; replaces SHOW_SENDERS)")
    ap.add_argument("--vban-bind", metavar="HOST:PORT", help="VBAN UDP socket (default 0.0.0.0:6980)")
    ap.add_argument("--bus-bind", metavar="HOST:PORT", help="bus and API (default 127.0.0.1:8703)")
    ap.add_argument("--host", help="bus and API host (over --bus-bind)")
    ap.add_argument("--port", help="bus and API port (over --bus-bind; scripts/ctl passes it)")
    ap.add_argument("--latency-file", metavar="PATH", help="latency.json (sources.<stream>.acoustic_offset_ms)")
    ap.add_argument("--log-level", metavar="LEVEL", help="DEBUG|INFO|WARNING|ERROR")
    ap.add_argument("--stream-priority", metavar="A,B,...",
                    help="audio streams ranked first, best first (over SHOW_STREAM_PRIORITY)")
    ap.add_argument("--artnet-target", metavar="HOST[:PORT]|off",
                    help="ArtDmx out (over SHOW_ARTNET_TARGET; default off)")
    ap.add_argument("--no-brain", dest="brain", action="store_false", default=None,
                    help="no Stage-3 brain (director, Auto, API routes, test sources, listener): the rollback switch")
    ap.add_argument("--brain", dest="brain", action="store_true", default=None, help="the brain on (the default)")
    ap.add_argument("--show-file", metavar="PATH", help="show.yaml (default config/room/show.yaml)")
    ap.add_argument("--vibes-file", metavar="PATH", help="vibes.yaml (default config/room/vibes.yaml)")
    ap.add_argument("--state-dir", metavar="PATH", help="state.json and the logs (default ~/.local/state/"
                                                        "screen-machine/show)")
    return ap


# -- load -------------------------------------------------------------------------------------------------------------
def load_options(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> Options:
    """Args over env over defaults. Never raises for bad values (they become notes); ``--help`` exits as usual."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    notes: list[str] = []
    args, unknown = _parser().parse_known_args(argv)
    if unknown:
        notes.append(f"ignored unknown arguments: {' '.join(unknown)}")
    base = Config()

    # senders: --sender (any) replaces SHOW_SENDERS
    specs = args.sender if args.sender else _split_senders(env.get("SHOW_SENDERS", ""))
    senders: dict[str, Sender] = {}
    for spec in specs:
        try:
            s = parse_sender(spec)
        except ValueError as e:
            notes.append(f"sender skipped: {e}")
            continue
        if s.name in senders:
            notes.append(f"sender {s.name!r} given twice: the last one wins")
        senders[s.name] = s

    def bind(arg: Optional[str], key: str, default: tuple[str, int]) -> tuple[str, int]:
        text = arg if arg is not None else env.get(key)
        if text is None or not str(text).strip():
            return default
        try:
            return parse_bind(str(text), default)
        except ValueError as e:
            notes.append(f"{key.lower().replace('_', '-')}: {e}; using {default[0]}:{default[1]}")
            return default

    vban_bind = bind(args.vban_bind, "SHOW_VBAN_BIND", base.vban_bind)
    bus_bind = bind(args.bus_bind, "SHOW_BUS_BIND", base.bus_bind)
    if args.host is not None:
        if _HOST.match(args.host.strip()):
            bus_bind = (args.host.strip(), bus_bind[1])
        else:
            notes.append(f"--host {args.host!r} ignored")
    if args.port is not None:
        try:
            bus_bind = (bus_bind[0], _port(args.port))
        except ValueError as e:
            notes.append(f"--port: {e}; using {bus_bind[1]}")

    latency_text = args.latency_file if args.latency_file is not None else env.get("SHOW_LATENCY_FILE")
    latency_file = _path(latency_text) if latency_text and latency_text.strip() else base.latency_file

    level = (args.log_level if args.log_level is not None else env.get("SHOW_LOG_LEVEL")) or base.log_level
    level = level.strip().upper()
    if level == "WARN":
        level = "WARNING"
    if level not in LEVELS:
        notes.append(f"log level {level!r} unknown; using {base.log_level}")
        level = base.log_level

    priority = None
    priority_text = args.stream_priority if args.stream_priority is not None else env.get("SHOW_STREAM_PRIORITY")
    if priority_text:
        names = tuple(dict.fromkeys(s.strip() for s in priority_text.split(",") if s.strip()))
        bad = [n for n in names if len(n) > STREAM_NAME_MAX or not n.isprintable()]
        if bad:
            notes.append(f"stream priority: skipped bad names {bad}")
        priority = tuple(n for n in names if n not in bad) or None

    artnet = None
    artnet_text = args.artnet_target if args.artnet_target is not None else env.get("SHOW_ARTNET_TARGET")
    if artnet_text and artnet_text.strip().lower() not in ("off", "none", "0", "false"):
        try:
            artnet = parse_bind(artnet_text, ("", ARTNET_PORT))
            if not artnet[0] or not artnet[1]:
                raise ValueError("no host or port")
        except ValueError as e:
            notes.append(f"artnet target {artnet_text!r}: {e}; Art-Net out off")
            artnet = None

    brain = True
    if args.brain is not None:
        brain = bool(args.brain)
    elif env.get("SHOW_BRAIN") is not None and env.get("SHOW_BRAIN", "").strip():
        word = env["SHOW_BRAIN"].strip().lower()
        if word in ("off", "0", "false", "no", "none"):
            brain = False
        elif word not in ("on", "1", "true", "yes"):
            notes.append(f"SHOW_BRAIN {env['SHOW_BRAIN']!r} isn't on or off; the brain stays on")

    def path_opt(arg: Optional[str], key: str, default: pathlib.Path) -> pathlib.Path:
        text = arg if arg is not None else env.get(key)
        return _path(text) if text and text.strip() else default

    cfg = Config(senders=tuple(senders.values()), vban_bind=vban_bind, bus_bind=bus_bind,
                 latency_file=latency_file, log_level=level, stream_priority=priority or ())
    return Options(config=cfg, stream_priority=priority, artnet_target=artnet, notes=tuple(notes), brain=brain,
                   show_file=path_opt(args.show_file, "SHOW_FILE", contract.SHOW_FILE),
                   vibes_file=path_opt(args.vibes_file, "SHOW_VIBES_FILE", contract.VIBES_FILE),
                   state_dir=path_opt(args.state_dir, "SHOW_STATE_DIR", contract.STATE_DIR))


def load(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> Config:
    """``load_options(...).config``, logging what was wrong."""
    opts = load_options(argv, env)
    for n in opts.notes:
        log.warning("config: %s", n)
    return opts.config


# -- latency.json -----------------------------------------------------------------------------------------------------
def _read_offsets(path: pathlib.Path) -> Optional[dict[str, float]]:
    """Offsets, ``{}`` if the file or its sources are absent, None if it exists but isn't valid JSON."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except Exception:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    sources = obj.get("sources") if isinstance(obj, dict) else None
    out: dict[str, float] = {}
    if isinstance(sources, dict):
        for name, entry in sources.items():
            v = entry.get("acoustic_offset_ms") if isinstance(entry, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out[str(name)] = float(v)
    return out


def acoustic_offsets(path: pathlib.Path) -> dict[str, float]:
    """``sources.<stream>.acoustic_offset_ms`` (signed ms, + = the sound reaches the listener later); ``{}`` if
    absent or unreadable; never raises."""
    try:
        return _read_offsets(path) or {}
    except Exception:
        return {}


class LatencyWatch:
    """Polls latency.json: ``poll()`` returns the offsets when the file changed (mtime or size; appearing or
    disappearing counts), else None. An invalid file (a torn write) returns None and is retried at the next poll, so
    the last good offsets stay. Main thread only."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self._stamp: object = object()          # nothing seen yet
        self.last: dict[str, float] = {}
        self.errors = 0

    def _stat(self) -> Optional[tuple[int, int]]:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def poll(self, force: bool = False) -> Optional[dict[str, float]]:
        stamp = self._stat()
        if not force and stamp == self._stamp:
            return None
        try:
            offsets = _read_offsets(self.path) if stamp is not None else {}
        except Exception:
            offsets = None
        if offsets is None:
            self.errors += 1
            return None
        self._stamp, self.last = stamp, offsets
        return dict(offsets)
