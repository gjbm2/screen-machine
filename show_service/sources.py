"""Test sources and the sender list (README "Stage 3", S3.10): the brain component ``sources``.

- **Senders, hot-reloaded from show.yaml.** On ``configure()`` and on every test start or stop it builds the
  effective sender list and hands it to the receiver (``ctx.set_senders``) when it changed:
  - the static senders (``--sender`` / SHOW_SENDERS) whose names show.yaml doesn't define;
  - plus show.yaml's senders that are not ``on_demand``;
  - plus the ``on_demand`` senders whose test is running.
  Each becomes ``Sender(name, host=hosts[0], port, streams, hosts=tuple(hosts), on_demand)``: ``hosts`` is the
  ordered fallback list the receiver tries (device addresses live in config/room/, never in code). A bad entry is
  skipped and listed in ``errors``.
- **Test sources** (``POST/DELETE/GET /api/v1/test``): ``piano`` plays a .mid (or a built-in phrase) through 98's
  ``tools/show_sender/show_sender.py --test-piano``; ``audio`` plays a music file through ``show_service.test_audio``
  as the stream ``test-audio``. Both are children of show_service on localhost, bound to 127.0.0.1 on their show.yaml
  sender's port, and are subscribed only while they run (``on_demand``). While a source with ``rank_first: true``
  plays, its stream outranks the room's (``ctx.set_priority``).
- **Children** are started with ``start_new_session=True``, ``cwd=REPO_ROOT`` and ``PYTHONUNBUFFERED=1``, logging to
  ``STATE_DIR/test-<source>.log`` (truncated at each start), through PDEATH_SHIM (they die with show_service). States:
  ``starting`` until the stream is ``streaming`` in ``ctx.health()``, then ``playing``; ``stopping`` after SIGTERM
  (SIGKILL to its whole process group 2 s later, so test audio's ffmpeg goes too); ``failed`` when it exited by
  itself (``error`` = its last 5 log lines); ``off``. Only the Popen objects made here (and the process groups they
  lead) are ever signalled. A piece switch stops the running child and starts the new one once the old has let go of
  the port.

Nothing here branches on a source's name: ``PLAYERS`` (built from contract.TEST_SOURCE_PLAYERS) maps the contract's
test-source words to the program that plays them; senders, ports, streams, folders and ranking are show.yaml's. Everything runs on show-brain and never blocks (spawning is a fork/exec; exits are polled), except
``stop()`` at shutdown, which waits at most 2 s for the children.

**The test piano's sound, output and recordings** (the Room page, 22 Oct; the show_sender side is another builder's):
- ``instruments()`` reads config/piano/instruments.json (``INSTRUMENTS_FILE``; re-read every RELOAD_S): an instrument is
  offered when its engine is ``windows-gs`` (kind "live": PC22's GS synth through pc22_synth, today's sound) or its
  ``file`` exists under its ``root`` (or the file's top-level ``root``; ~ expanded); ordered by ``order``. No file: the
  windows-gs entry alone. ``default_instrument``: the file's ``default`` when offered, else the windows-gs one, else
  the first. ``OUTPUTS``: "pc22" (PC22 speakers) or "none" (screens only), plus **Play on Sonos** (22 Oct): one
  ``"sonos:<zone>"`` ("Sonos (<room>)") per zone in config/room/sonos.json (``SONOS_FILE``, room_service's; re-read
  every RELOAD_S; a zone key outside NAME_OK is skipped and listed in ``errors``), offered while a rendered sound is
  offered and the piano script names ``"sonos:"`` and ``--title`` (SONOS_FLAGS). The Windows synth plays only on
  PC22: asked for on Sonos, it plays on PC22 and the status says so in ``error`` (degrade, never refuse).
- ``start_test(source, piece, instrument, output, once)``: the piano's defaults are windows-gs (a POST without an
  instrument keeps today's sound for scripts and other sessions; the Room page always names one), "pc22", and
  once = False for a library piece, True for ``rec:<id>`` (a recording in recorder.py's folder, played by its absolute
  path; never listed in ``pieces()``). ValueError("unknown_instrument" | "unknown_output"). The Music page's
  ``lib:<id>`` and ``user:<id>`` pieces (library.py, 8f 22 Oct) play exactly like ``rec:``: library.resolve gives the
  absolute .mid (else "unknown_piece"), the title is the label, once by default, never in ``pieces()``.
- argv: windows-gs + pc22 + loop is exactly today's; windows-gs + none adds ``--no-speaker``; another instrument adds
  ``--instrument ID --output pc22|none|sonos:<zone> --status-file STATE_DIR/test-piano.status.json`` (removed before
  each start), ``--title LABEL`` (the piece's label: what the Sonos app shows) on Sonos, and ``--lead-ms L`` when the
  runtime offset below is applied; ``--once`` when once. The status file
  (``{"stage": "preparing"|"playing"|"done"|"error", "progress", "error"}``) is read on every poll: ``preparing``
  while it says so (fast-polled like ``starting``, never timed out), ``progress`` and an ``error`` from it; a once
  child that exits 0 is ``finished``.
- **An older show_sender keeps today's argv**: each new flag (SENDER_FLAGS) is passed only when the piano script's
  text names it (its argparse; re-read when the script changes). Without ``--instrument``/``--output``/
  ``--status-file`` only the windows-gs sound is offered (and is the default, whatever the file says); without
  ``--once`` a run loops; without ``--lead-ms`` no offset is applied.
- **Runtime acoustic offset** (tests/test_future_offset.py checks the engine with notes up to 0.6 s ahead): while a
  rendered run is heard (an instrument other than windows-gs, output not "none": PC22 or Sonos) the test piano's
  stream gets acoustic_offset_ms = L (show.yaml ``tests.piano.lead_ms``, default LEAD_MS_DEFAULT 650) through
  ``set_offset_fn`` (``__main__.Show.set_runtime_offset``: merged over latency.json's offsets, then
  Receiver.set_offsets); removed when the run ends. Without that seam no offset is applied and ``--lead-ms`` isn't
  passed.
- **The calibration mic** (1c, 23 Oct: piano_render/calibrate.py hears the rendered run through the mic in the room it
  plays in). A rendered run that is heard gets ``--calibration-mic STREAM`` (when the piano script names it): the
  room-sound stream of the show.yaml sender whose ``label`` (or name) is the output's room: a Sonos zone's ``room``
  in SONOS_FILE (else the zone key), or the output id for "pc22"; case, spaces, hyphens and underscores ignored;
  the sender's first stream that is room sound as inputs.Inputs counts it (not ``on_demand``, not ``room_sound:
  false``, not a test or MIDI stream). show.yaml ``tests.piano.calibration_mic`` overrides it: a stream name for every
  output, or ``{<output id>: <stream> | null}``. Nothing found: ``--calibration-mic ""`` (no mic check; the session
  says so). While a run on a **Sonos** output is RUNNING, that stream is claimed through ``set_mic_claim_fn``
  (``__main__``: ``Mic.claim_stream(stream, by="test", label="Test piano", on=...)``, so it captures as well; Mute
  still wins) and released when the run ends, fails, is stopped, is replaced by one that doesn't need it, or the
  service stops. PC22's mic is passed but never claimed: the owner's Mic choice governs it. Without the seam
  nothing is claimed (``errors`` says so while such a run plays).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Collection, Optional

from . import contract, library, recorder
from .contract import Sender

log = logging.getLogger("show.sources")

MIDI_EXTS = (".mid", ".midi")
AUDIO_EXTS = (".mp3", ".ogg", ".opus", ".flac", ".wav", ".m4a")
KILL_AFTER_S = 2.0                     # SIGTERM, then SIGKILL this long after
FAST_POLL_S = 0.1                      # while a child is starting/stopping (else the 1 Hz tick)
FAST_POLL_FOR_S = 10.0                 # a "starting" child is fast-polled this long, then left to the tick
NO_HEALTH_PLAYING_S = 2.0              # without ctx.health, alive this long counts as playing
LOG_TAIL_LINES = 5
LOG_HEADER = "# show_service starts: "            # the first line of a child's log (left out of ``error``)
LOCAL_BIND = "127.0.0.1"
RUNNING = ("starting", "playing", "preparing")
STARTING = ("starting", "preparing")             # fast-polled; "preparing": the sound is being rendered
#: the test piano's sounds (module docstring); the other builder's file, read only
INSTRUMENTS_FILE = contract.REPO_ROOT / "config/piano/instruments.json"
LIVE_ENGINE = "windows-gs"
LIVE_INSTRUMENT = {"id": "windows-gs", "label": "Basic (Windows synth)", "kind": "live"}
OUTPUTS: tuple[dict[str, str], ...] = ({"id": "pc22", "label": "PC22 speakers"}, {"id": "none", "label": "Screens only"})
DEFAULT_OUTPUT = "pc22"
SILENT_OUTPUT = "none"
LEAD_MS_DEFAULT = 650.0                # show.yaml tests.piano.lead_ms: the rendered sound starts this long after its MIDI
LEAD_MS_MAX = 1900.0                   # below stream_service sound_view's FUTURE_MAX_MS (2000)
REC_PREFIX = "rec:"
LIBRARY_PREFIXES = (library.LIB_PREFIX, library.USER_PREFIX)   # the Music page's pieces, played by absolute path
#: show_sender's newer flags (the other builder's): passed only when its script takes them (its text names them), so
#: an older show_sender keeps today's argv; the rendered sounds are offered only with RENDER_FLAGS
SENDER_FLAGS: tuple[str, ...] = ("--instrument", "--output", "--status-file", "--lead-ms", "--once", "--title",
                                 "sonos:", "--calibration-mic", "--speed", "--start-s")
SPEED_RANGE = (0.25, 4.0)                        # a piece's tempo times this (owner, 23 Oct: "a speed/pacing control
                                                 # setting (1X obviously the default)"); show_sender's --speed
CALIBRATION_FLAG = "--calibration-mic"           # show_sender -> piano_render.session -> calibrate.py's mic
CALIBRATION_KEY = "calibration_mic"              # show.yaml tests.piano.calibration_mic (module docstring)
RENDER_FLAGS = frozenset(("--instrument", "--output", "--status-file"))
SONOS_PREFIX = "sonos:"                # an output "sonos:<zone>": a zone of SONOS_FILE (Play on Sonos)
SONOS_FLAGS = frozenset(("--title", SONOS_PREFIX))     # show_sender's Sonos output (its text names "sonos:")
#: room_service's Sonos zones (screen-machine-ff's file, read only): {"zones": {<zone>: {"room", "ip", ...}}}
SONOS_FILE = contract.REPO_ROOT / "config/room/sonos.json"
LIVE_ON_SONOS = "The Windows synth plays on PC22 only, so it's playing there; pick a rendered sound for Sonos."
PIANO_REPLAY = contract.REPO_ROOT / "stream_service" / "piano_replay.py"   # PHRASE_LABELS, read by file path
NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

#: Each player kind: the show.yaml key of its folder, the files it lists, and the folder when show.yaml leaves it out.
KINDS: dict[str, dict[str, Any]] = {
    "midi": {"dir_key": "pieces", "exts": MIDI_EXTS, "default_dir": contract.TEST_PIANO_DIR},
    "audio": {"dir_key": "music", "exts": AUDIO_EXTS, "default_dir": contract.TEST_MUSIC_DIR},
}
#: How each test source is played: contract.TEST_SOURCE_PLAYERS (the kind and the documented defaults) joined with its
#: kind's folder and files. ``default_*`` are used only when show.yaml leaves ``tests.<source>`` (or its sender) out.
PLAYERS: dict[str, dict[str, Any]] = {
    src: {"kind": p["kind"], **KINDS[p["kind"]], "default_sender": p["sender"], "default_port": p["port"],
          "default_stream": p["stream"]}
    for src, p in contract.TEST_SOURCE_PLAYERS.items()}
assert tuple(PLAYERS) == contract.TEST_SOURCES
#: The children's parent-death guard (README S3.10): run as ``python -c PDEATH_SHIM <show_service pid> <argv...>``, it
#: asks Linux to SIGKILL it when show_service dies (PR_SET_PDEATHSIG survives the exec), checks the parent is still
#: there (it may have died before the prctl), then execs the real program. A crash, a SIGKILL or a slow stop of
#: show_service can then never leave a test child holding its UDP port.
PDEATH_SHIM = ("import ctypes, os, signal, sys\n"
               "try:\n"
               "    ctypes.CDLL(None, use_errno=True).prctl(1, int(signal.SIGKILL), 0, 0, 0)\n"
               "except Exception:\n"
               "    pass\n"
               "if os.getppid() != int(sys.argv[1]):\n"
               "    sys.exit(0)\n"
               "os.execv(sys.argv[2], sys.argv[2:])\n")


# -- senders (pure) ---------------------------------------------------------------------------------------------------
def parse_senders(settings: dict) -> tuple[list[Sender], list[str]]:
    """show.yaml ``senders`` -> (valid Senders in file order, problems). Bad entries are skipped."""
    raw = (settings or {}).get("senders")
    if raw is None:
        return [], []
    if not isinstance(raw, dict):
        return [], ["senders: not a mapping (ignored)"]
    out, errors = [], []
    for name, spec in raw.items():
        where = f"senders.{name}"
        if not isinstance(name, str) or not name or not set(name) <= NAME_OK:
            errors.append(f"{where}: bad name (skipped)")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{where}: not a mapping (skipped)")
            continue
        hosts = spec.get("hosts", spec.get("host"))
        if isinstance(hosts, str):
            hosts = [hosts]
        if (not isinstance(hosts, list) or not hosts
                or not all(isinstance(h, str) and h.strip() and " " not in h.strip() for h in hosts)):
            errors.append(f"{where}.hosts: needs a list of host names or addresses (skipped)")
            continue
        port = spec.get("port", 6980)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(f"{where}.port: {port!r} is not a UDP port (skipped)")
            continue
        streams = spec.get("streams")
        if isinstance(streams, str):
            streams = [streams]
        if (not isinstance(streams, list) or not streams
                or not all(isinstance(s, str) and s and set(s) <= NAME_OK for s in streams)):
            errors.append(f"{where}.streams: needs a list of stream names (skipped)")
            continue
        on_demand = spec.get("on_demand", False)
        if not isinstance(on_demand, bool):
            errors.append(f"{where}.on_demand: {on_demand!r} is not true/false (skipped)")
            continue
        hs = tuple(h.strip() for h in hosts)
        out.append(Sender(name, hs[0], port, tuple(streams), hosts=hs, on_demand=on_demand))
    return out, errors


def senders_from_settings(settings: dict, static: tuple[Sender, ...],
                          running: Collection[str]) -> tuple[Sender, ...]:
    """The effective sender list: static senders show.yaml doesn't define, show.yaml's always-on senders, and the
    on_demand ones named in ``running`` (sender names whose test is running). Pure."""
    yaml_senders, _ = parse_senders(settings)
    defined = {s.name for s in yaml_senders}
    eff = [s for s in static if s.name not in defined]
    eff += [s for s in yaml_senders if not s.on_demand or s.name in running]
    return tuple(eff)


# -- pieces ------------------------------------------------------------------------------------------------------------
_phrases: Optional[dict[str, str]] = None
_phrases_error: Optional[str] = None


def phrase_labels() -> dict[str, str]:
    """piano_replay.PHRASE_LABELS, loaded once by file path ({} if that fails)."""
    global _phrases, _phrases_error
    if _phrases is None:
        try:
            spec = importlib.util.spec_from_file_location("_show_sources_piano_replay", PIANO_REPLAY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)                       # type: ignore[union-attr]
            _phrases = {str(k): str(v) for k, v in dict(mod.PHRASE_LABELS).items()}
        except Exception as e:                                 # noqa: BLE001  degrade: no built-in phrases
            _phrases, _phrases_error = {}, f"piano phrases unavailable: {type(e).__name__}: {e}"
    return _phrases


def _label(stem: str) -> str:
    text = " ".join(stem.replace("_", " ").split())
    return text[:1].upper() + text[1:]


def _files(folder: pathlib.Path, exts: tuple[str, ...]) -> list[pathlib.Path]:
    try:
        return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts and
                       not p.name.startswith(".")), key=lambda p: p.name.lower())
    except OSError:
        return []


def midi_pieces(folder: pathlib.Path) -> list[contract.PieceInfo]:
    out: list[contract.PieceInfo] = [{"id": f"file:{p.name}", "label": _label(p.stem), "kind": "file"}
                                     for p in _files(folder, MIDI_EXTS)]
    out += [{"id": k, "label": v, "kind": "phrase"} for k, v in phrase_labels().items()]
    return out


def audio_pieces(folder: pathlib.Path) -> list[contract.PieceInfo]:
    """Audio files in ``folder``, labelled from its index.json (tools/show/fetch_test_music.py) where it knows them."""
    by_file: dict[str, dict] = {}
    try:
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        if isinstance(index, dict):
            by_file = {e["file"]: e for e in index.values() if isinstance(e, dict) and isinstance(e.get("file"), str)}
    except (OSError, ValueError):
        pass
    out: list[contract.PieceInfo] = []
    for p in _files(folder, AUDIO_EXTS):
        e = by_file.get(p.name, {})
        info: contract.PieceInfo = {"id": f"file:{p.name}", "label": str(e.get("title") or _label(p.stem)),
                                    "kind": "file"}
        for key in ("genre", "artist", "license"):
            if isinstance(e.get(key), str) and e[key]:
                info[key] = e[key]                             # type: ignore[literal-required]
        if isinstance(e.get("duration_s"), (int, float)) and not isinstance(e.get("duration_s"), bool):
            info["duration_s"] = float(e["duration_s"])
        out.append(info)
    return out


# -- the test piano's sounds and status (pure) --------------------------------------------------------------------------
def _num_or(v: Any, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else default


def load_instruments(path: pathlib.Path = INSTRUMENTS_FILE) -> tuple[list[dict], Optional[str], list[str]]:
    """config/piano/instruments.json -> (the offered instruments in order, each ``{"id", "label", "kind", "engine"}``;
    the file's ``default``; problems). A missing file: the windows-gs entry alone; an unreadable one: the same, and a
    problem. Shapes taken: ``{"root"?, "default"?, "instruments": [{id, label, engine, file, root?, order?}] | {id:
    {...}}}`` or a bare list."""
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [dict(LIVE_INSTRUMENT, engine=LIVE_ENGINE)], None, []
    except (OSError, ValueError) as e:
        return ([dict(LIVE_INSTRUMENT, engine=LIVE_ENGINE)], None,
                [f"{path.name}: not read ({type(e).__name__}: {e}): only the Windows synth is offered"])
    top = doc if isinstance(doc, dict) else {}
    raw = top.get("instruments") if isinstance(doc, dict) else doc
    if isinstance(raw, dict):
        raw = [{"id": k, **v} for k, v in raw.items() if isinstance(v, dict)]
    if not isinstance(raw, list):
        return [dict(LIVE_INSTRUMENT, engine=LIVE_ENGINE)], None, [f"{path.name}: no instruments list"]
    root_top = top.get("root") if isinstance(top.get("root"), str) else None
    rows, problems = [], []
    for i, spec in enumerate(raw):
        if not isinstance(spec, dict):
            continue
        iid, engine = spec.get("id"), spec.get("engine")
        if not isinstance(iid, str) or not iid or not set(iid) <= NAME_OK:
            problems.append(f"{path.name}: instrument {i}: bad id (skipped)")
            continue
        label = spec.get("label") if isinstance(spec.get("label"), str) and spec.get("label") else iid
        if engine == LIVE_ENGINE:
            kind = "live"
        else:
            f = spec.get("file")
            if not isinstance(f, str) or not f.strip():
                continue                                 # nothing to render with: not offered
            p = pathlib.Path(f.strip()).expanduser()
            if not p.is_absolute():
                root = spec.get("root") if isinstance(spec.get("root"), str) else root_top
                base = pathlib.Path(root).expanduser() if root else contract.REPO_ROOT
                p = (base if base.is_absolute() else contract.REPO_ROOT / base) / p
            try:
                if not p.exists():
                    continue                             # not installed (yet)
            except OSError:
                continue
            kind = "rendered"
        rows.append(((_num_or(spec.get("order"), 1e9), i), {"id": iid, "label": label, "kind": kind,
                                                             "engine": engine if isinstance(engine, str) else None}))
    rows.sort(key=lambda r: r[0])
    out, seen = [], set()
    for _, r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    default = top.get("default", top.get("default_instrument"))
    return out, default if isinstance(default, str) else None, problems


def load_sonos_outputs(path: pathlib.Path = SONOS_FILE) -> tuple[list[dict], list[str]]:
    """config/room/sonos.json -> (the Sonos outputs in file order, ``{"id": "sonos:<zone>", "label": "Sonos
    (<room>)"}``; problems). A missing file: none, quietly; an unreadable one or a bad zone key: listed."""
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError) as e:
        return [], [f"{pathlib.Path(path).name}: not read ({type(e).__name__}: {e}): no Sonos to play on"]
    zones = doc.get("zones") if isinstance(doc, dict) else None
    if not isinstance(zones, dict):
        return [], [f"{pathlib.Path(path).name}: no zones: no Sonos to play on"]
    out, problems = [], []
    for zone, spec in zones.items():
        if not isinstance(zone, str) or not zone or not set(zone) <= NAME_OK or not isinstance(spec, dict):
            problems.append(f"{pathlib.Path(path).name}: zone {zone!r}: bad name or entry (skipped)")
            continue
        room = spec.get("room") if isinstance(spec.get("room"), str) and spec.get("room").strip() else zone
        out.append({"id": f"{SONOS_PREFIX}{zone}", "label": f"Sonos ({room.strip()})"})
    return out, problems


def sonos_rooms(path: pathlib.Path = SONOS_FILE) -> dict[str, str]:
    """config/room/sonos.json -> {zone: its Sonos room name} ({} when it can't be read; load_sonos_outputs says why)."""
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    zones = doc.get("zones") if isinstance(doc, dict) else None
    if not isinstance(zones, dict):
        return {}
    return {z: (s["room"].strip() if isinstance(s.get("room"), str) and s["room"].strip() else z)
            for z, s in zones.items() if isinstance(z, str) and isinstance(s, dict)}


def _place(text: str) -> str:
    """A room or sender name compared loosely: case, spaces, hyphens and underscores ignored."""
    return "".join(ch for ch in text.lower() if not ch.isspace() and ch not in "-_")


def calibration_mic(settings: dict, output: Optional[str], *, rooms: Optional[dict[str, str]] = None,
                    health: Optional[dict] = None, override: Any = None) -> tuple[Optional[str], str]:
    """The mic stream that hears ``output`` (module docstring, "The calibration mic"): (stream, where it came from), or
    (None, why none) in plain words. ``rooms``: sonos_rooms(); ``health``: the receiver's (to leave out MIDI streams
    it has classified); ``override``: show.yaml ``tests.piano.calibration_mic``. Pure."""
    if not output or output == SILENT_OUTPUT:
        return None, "nothing is heard"
    if isinstance(override, dict) and output in override:
        v = override[output]
        if v is None or v == "":
            return None, f"show.yaml tests.piano.{CALIBRATION_KEY} names no mic for {output}"
        override = v
    if isinstance(override, str) and override and set(override) <= NAME_OK:
        return override, f"show.yaml tests.piano.{CALIBRATION_KEY}"
    if output.startswith(SONOS_PREFIX):
        zone = output[len(SONOS_PREFIX):]
        room = (rooms or {}).get(zone) or zone
        names, where = {_place(room), _place(zone)}, f"Sonos ({room})"
    else:
        names, where = {_place(output)}, output
    raw = settings.get("senders") if isinstance(settings, dict) else None
    specs = raw if isinstance(raw, dict) else {}
    streams_h = (health or {}).get("streams") if isinstance(health, dict) else None
    streams_h = streams_h if isinstance(streams_h, dict) else {}
    for s in parse_senders(settings)[0]:
        spec = specs.get(s.name) if isinstance(specs.get(s.name), dict) else {}
        if s.on_demand or spec.get("room_sound") is False:
            continue
        label = spec["label"].strip() if isinstance(spec.get("label"), str) and spec["label"].strip() else s.name
        if _place(label) not in names and _place(s.name) not in names:
            continue
        for st in s.streams:
            m = streams_h.get(st) if isinstance(streams_h.get(st), dict) else {}
            kind = m.get("kind")
            if (st in contract.TEST_STREAMS or st.startswith("test-") or m.get("test") or kind == "midi"
                    or (kind in (None, "unknown") and st in contract.MIDI_STREAMS)):
                continue
            return st, f"show.yaml sender {s.name} ({label})"
    return None, f"no room mic in show.yaml's senders is labelled for {where}"


def sender_flags(script: pathlib.Path) -> frozenset:
    """The SENDER_FLAGS a show_sender script takes: the ones its text names as a quoted literal (its argparse)."""
    try:
        text = pathlib.Path(script).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    return frozenset(f for f in SENDER_FLAGS if f'"{f}"' in text or f"'{f}'" in text)


def read_status(path: Optional[pathlib.Path]) -> Optional[dict]:
    """show_sender's status file: ``{"stage", "progress" (0..1 or None), "error", "pos_s"}`` (pos_s: the place in the
    piece at its own tempo, a newer show_sender's); None while it is missing or torn."""
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("stage") not in ("preparing", "playing", "done", "error"):
        return None
    prog = doc.get("progress")
    prog = min(1.0, max(0.0, float(prog))) if _num_or(prog, -1.0) >= 0.0 else None
    err = doc.get("error")
    pos = doc.get("pos_s")
    at = doc.get("at")
    return {"stage": doc["stage"], "progress": prog, "error": err if isinstance(err, str) and err else None,
            "pos_s": float(pos) if _num_or(pos, -1.0) >= 0.0 else None,
            "pos_at": float(at) if _num_or(at, -1.0) > 0.0 else None}


# -- the component -----------------------------------------------------------------------------------------------------
class _Child:
    """One requested run of a test source: pending (proc None) until the port is free, then a process."""

    def __init__(self, source: str, piece: str, label: str, sender: Sender, stream: str, now: float) -> None:
        self.source, self.piece, self.label = source, piece, label
        self.sender, self.stream = sender, stream
        self.proc: Optional[subprocess.Popen] = None
        self.state = "starting"
        self.since_s = now
        self.started_s: Optional[float] = None
        self.term_s: Optional[float] = None
        self.killed = False
        self.error: Optional[str] = None
        self.log_path: Optional[pathlib.Path] = None
        # the test piano's sound (module docstring): None/False for test audio and for today's defaults
        self.instrument: Optional[str] = None
        self.live = True                                 # the windows-gs engine (today's sound), or nothing chosen
        self.output: Optional[str] = None
        self.once = False
        self.path: Optional[pathlib.Path] = None         # a rec: piece's .mid
        self.lead_ms: Optional[float] = None             # the runtime acoustic offset this run applies
        self.status_path: Optional[pathlib.Path] = None
        self.stage: Optional[str] = None
        self.progress: Optional[float] = None
        self.status_error: Optional[str] = None
        self.note: Optional[str] = None                  # said in ``error`` while it runs (a degraded choice)
        self.cal_mic: Optional[str] = None               # --calibration-mic ("" = none hears it); None: not heard
        self.cal_why: Optional[str] = None               # where cal_mic came from, or why there is none
        self.claim: Optional[str] = None                 # the mic stream claimed while it runs (Sonos only)
        self.speed = 1.0                                 # the piece's tempo times this (--speed)
        self.start_s = 0.0                               # where in the piece it starts (--start-s, its own tempo)
        self.pos_s: Optional[float] = None               # where in the piece it is now (show_sender's status)
        self.pos_at: Optional[float] = None              # ... as of this CLOCK_MONOTONIC time (show_sender's)

    def set(self, state: str, now: float) -> None:
        if state != self.state:
            self.state, self.since_s = state, now


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL a child and its process group (it leads one: start_new_session), so test audio's ffmpeg goes too."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


class Sources:
    """Component ``sources`` (tick 1 Hz): the sender list and the two test children."""

    name = "sources"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, python: str = sys.executable,
                 piano_script: pathlib.Path = contract.TEST_PIANO_SCRIPT,
                 audio_cmd: Optional[list[str]] = None, log_dir: pathlib.Path = contract.STATE_DIR,
                 clock: Callable[[], float] = time.monotonic,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen, pdeath: bool = True,
                 instruments_file: pathlib.Path = INSTRUMENTS_FILE,
                 recordings_dir: pathlib.Path = recorder.RECORDINGS_DIR,
                 set_offset: Optional[Callable[[str, Optional[float]], None]] = None,
                 sonos_file: pathlib.Path = SONOS_FILE,
                 set_mic_claim: Optional[Callable[[str, bool], Any]] = None) -> None:
        """``pdeath``: run each child through PDEATH_SHIM (off only for a ``popen`` that can't take it).
        ``instruments_file``, ``recordings_dir`` (show.yaml ``tests.piano.recordings`` wins), ``sonos_file``: tests
        pass tmp ones.
        ``set_offset(stream, ms | None)``: the runtime acoustic offset seam (also :meth:`set_offset_fn`).
        ``set_mic_claim(stream, on)``: the calibration mic's claim seam (also :meth:`set_mic_claim_fn`)."""
        self.instruments_file = pathlib.Path(instruments_file)
        self.recordings_dir = pathlib.Path(recordings_dir)
        self._set_offset = set_offset
        self._mic_claim = set_mic_claim
        self._claim_applied: Optional[str] = None
        self._instr: Optional[tuple[list[dict], Optional[str], list[str]]] = None
        self._instr_at = -1e18
        self.sonos_file = pathlib.Path(sonos_file)
        self._sonos: Optional[tuple[list[dict], list[str]]] = None
        self._sonos_at = -1e18
        self._flags: tuple[Any, frozenset] = (None, frozenset())
        self._offsets_applied: dict[str, float] = {}
        self.ctx = ctx
        self.pdeath = bool(pdeath)
        self.python, self.piano_script = python, pathlib.Path(piano_script)
        self.audio_cmd = list(audio_cmd) if audio_cmd else [python, "-m", "show_service.test_audio"]
        self.log_dir = pathlib.Path(log_dir)
        self.clock, self.popen = clock, popen
        self.settings: dict = {}
        self._cur: dict[str, Optional[_Child]] = {s: None for s in PLAYERS}
        self._retiring: list[_Child] = []
        self._applied: Optional[tuple[Sender, ...]] = None
        self._prio_applied: Optional[tuple[str, ...]] = None
        self._sender_errors: list[str] = []
        self._apply_errors: list[str] = []
        self._pieces: Optional[dict[str, list[contract.PieceInfo]]] = None
        self._pieces_at = -1e18
        self._fast_handle: Any = None
        self._wanted: tuple[tuple[Sender, ...], tuple[str, ...]] = ((), ())
        self._snap: dict = {}
        self._rebuild()

    # -- Component -----------------------------------------------------------------------------------------------------
    def provides(self) -> dict[str, Any]:
        return {}

    def configure(self, settings: dict) -> None:
        self.settings = settings if isinstance(settings, dict) else {}
        self._pieces = None
        self._apply()
        self._rebuild()

    def restore(self, saved: dict) -> None:
        """Nothing persists: a restart stops the tests."""

    def start(self) -> None:
        self._apply()
        self._rebuild()

    def tick(self, now_s: float) -> None:
        self._poll(self.clock())

    def snapshot(self) -> dict:
        return self._snap

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> None:
        """Shutdown: SIGTERM every child made here, wait up to KILL_AFTER_S, then SIGKILL what is left."""
        if self._fast_handle is not None:
            self._fast_handle.cancel()
            self._fast_handle = None
        now = self.clock()
        children = [c for c in list(self._cur.values()) + self._retiring if c is not None and c.proc is not None]
        for c in children:
            self._terminate(c, now)
        deadline = time.monotonic() + KILL_AFTER_S
        for c in children:
            try:
                c.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _kill(c.proc)
                try:
                    c.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    log.error("test %s: pid %s didn't exit after SIGKILL", c.source, c.proc.pid)
        self._retiring = []
        for c in self._cur.values():
            if c is not None and c.state in RUNNING + ("stopping",):
                c.set("off", now)
        self._apply()
        self._rebuild()

    # -- API (show-brain) ----------------------------------------------------------------------------------------------
    def pieces(self) -> dict[str, list[contract.PieceInfo]]:
        now = self.clock()
        if self._pieces is None or now - self._pieces_at >= contract.RELOAD_S:
            out: dict[str, list[contract.PieceInfo]] = {}
            for source, player in PLAYERS.items():
                folder = self._folder(source)
                out[source] = midi_pieces(folder) if player["kind"] == "midi" else audio_pieces(folder)
            self._pieces, self._pieces_at = out, now
        return self._pieces

    def instruments(self) -> tuple[list[dict], Optional[str]]:
        """The test piano's offered sounds (``{"id", "label", "kind"}``, in order) and the default's id."""
        rows, default = self._instruments()
        return [{"id": r["id"], "label": r["label"], "kind": r["kind"]} for r in rows], default

    def choices(self) -> dict:
        """GET /api/v1/test's ``instruments``, ``default_instrument`` and ``outputs``."""
        rows, default = self.instruments()
        return {"instruments": rows, "default_instrument": default, "outputs": self._outputs()}

    def start_test(self, source: str, piece: Optional[str] = None, instrument: Optional[str] = None,
                   output: Optional[str] = None, once: Optional[bool] = None,
                   if_idle: bool = False, speed: Optional[float] = None,
                   start_s: Optional[float] = None) -> contract.SourceStatus:
        """Start (or switch) a test source. ValueError("unknown_source" | "unknown_piece" | "unknown_instrument" |
        "unknown_output" | "busy"). ``instrument``/``output``/``once`` are the piano's (module docstring); test audio
        ignores them. ``if_idle`` (scripts, 23 Oct: a check's start replaced the owner's playing piece): start only
        while the source is idle, else ValueError("busy"); a person's start (the pages) takes over as before.
        ``speed`` (the piano): the piece's tempo times this, SPEED_RANGE, 1 by default; ``start_s``: start that far
        into the piece (seconds at its own tempo; how a speed change carries on from where it was: its status's
        ``pos_s``). ValueError("bad_speed")."""
        if source not in PLAYERS:
            raise ValueError("unknown_source")
        if speed is not None and (not isinstance(speed, (int, float)) or isinstance(speed, bool)
                                  or not SPEED_RANGE[0] <= float(speed) <= SPEED_RANGE[1]):
            raise ValueError("bad_speed")
        cur = self._cur.get(source)
        if if_idle and cur is not None and cur.state in RUNNING + ("stopping",):
            raise ValueError("busy")
        midi = PLAYERS[source]["kind"] == "midi"
        path: Optional[pathlib.Path] = None
        if midi and isinstance(piece, str) and piece.startswith(REC_PREFIX):
            info, path = self._recording(piece)
        elif midi and isinstance(piece, str) and piece.startswith(LIBRARY_PREFIXES):
            info, path = self._library_piece(piece)
        else:
            choices = self.pieces()[source]
            if piece is None:
                if not choices:
                    raise ValueError("unknown_piece")
                info = choices[0]
            else:
                info = next((p for p in choices if p["id"] == piece), None)
                if info is None:
                    raise ValueError("unknown_piece")
        spec: Optional[dict] = None
        out = None
        if midi:
            rows, _ = self._instruments()
            iid = instrument if instrument is not None else LIVE_ENGINE   # unnamed: today's sound (not the file's default)
            spec = next((r for r in rows if r["id"] == iid), None)
            if spec is None:
                raise ValueError("unknown_instrument")
            out = output if output is not None else DEFAULT_OUTPUT
            if out not in {o["id"] for o in self._outputs()}:
                raise ValueError("unknown_output")
            note = None
            if out.startswith(SONOS_PREFIX) and spec.get("engine") == LIVE_ENGINE:
                out, note = DEFAULT_OUTPUT, LIVE_ON_SONOS          # the GS synth is PC22's: play it there, say so
        now = self.clock()
        old = self._cur.get(source)
        if old is not None and old.proc is not None and old.proc.poll() is None:
            self._terminate(old, now)
            self._retiring.append(old)
        sender, stream = self._test_sender(source)
        c = _Child(source, info["id"], info["label"], sender, stream, now)
        if spec is not None:
            flags = self._sender_flags()
            c.instrument, c.live, c.output = spec["id"], spec.get("engine") == LIVE_ENGINE, out
            c.once = (bool(once) if once is not None else path is not None) and "--once" in flags
            c.path = path
            c.note = note
            if "--speed" in flags:
                c.speed = float(speed) if speed is not None else 1.0
                c.start_s = max(0.0, float(start_s or 0.0)) if "--start-s" in flags else 0.0
                if c.live:                                 # a status file for the GS synth too: where it is
                    c.status_path = self.log_dir / f"test-{source}.status.json"
            if not c.live:
                c.status_path = self.log_dir / f"test-{source}.status.json"
                lead = self._lead_ms(source)
                if (out != SILENT_OUTPUT and lead is not None and self._set_offset is not None
                        and "--lead-ms" in flags):
                    c.lead_ms = lead
                if out != SILENT_OUTPUT:                       # heard: the mic that hears it (module docstring)
                    mic, c.cal_why = self._calibration_mic(source, out)
                    c.cal_mic = mic or ""
                    if mic and out.startswith(SONOS_PREFIX):
                        c.claim = mic                          # PC22's mic: the owner's Mic choice, never claimed
                    log.info("test %s: calibration mic %s (%s)%s", source, mic or "none", c.cal_why,
                             ", claimed while it plays" if c.claim else "")
        self._cur[source] = c
        log.info("test %s: %s (%s)%s", source, info["label"], info["id"],
                 "" if spec is None else f", {c.instrument} on {c.output}{', once' if c.once else ''}")
        self._poll(now)
        self._apply()
        self._rebuild()
        self._schedule()
        return self._status(source, now)

    def stop_test(self, source: Optional[str] = None, piece: Optional[str] = None) -> list[str]:
        """Stop one source (or all); returns the sources that were running. With ``piece`` (scripts and the player,
        23 Oct: a check's stop killed the owner's piece), only a source still playing that piece is stopped."""
        now = self.clock()
        stopped = []
        for src, c in self._cur.items():
            if c is None or (source is not None and src != source) or (piece is not None and c.piece != piece):
                continue
            if c.state in RUNNING:
                stopped.append(src)
                if c.proc is not None and c.proc.poll() is None:
                    self._terminate(c, now)
                    c.set("stopping", now)
                else:
                    c.set("off", now)
                log.info("test %s: stopped", src)
            elif c.state in ("failed", "finished"):
                c.set("off", now)
        self._poll(now)
        self._apply()
        self._rebuild()
        self._schedule()
        return stopped

    def status(self) -> dict[str, contract.SourceStatus]:
        now = self.clock()
        return {s: self._status(s, now) for s in PLAYERS}

    def set_offset_fn(self, fn: Optional[Callable[[str, Optional[float]], None]]) -> None:
        """``__main__``: the runtime acoustic offset seam (Show.set_runtime_offset), before the brain starts."""
        self._set_offset = fn

    def set_mic_claim_fn(self, fn: Optional[Callable[[str, bool], Any]]) -> None:
        """``__main__``: the calibration mic's claim seam (``fn(stream, on)``: Mic.claim_stream), before the brain
        starts."""
        self._mic_claim = fn

    # -- internals -----------------------------------------------------------------------------------------------------
    def _tests_cfg(self, source: str) -> dict:
        tests = self.settings.get("tests")
        cfg = tests.get(source) if isinstance(tests, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    def _instruments(self) -> tuple[list[dict], Optional[str]]:
        """(offered rows with their engine, the default id), instruments.json re-read every RELOAD_S. Nothing offered:
        the Windows synth (degrade to today's sound)."""
        now = self.clock()
        if self._instr is None or now - self._instr_at >= contract.RELOAD_S:
            self._instr, self._instr_at = load_instruments(self.instruments_file), now
        rows, want, _ = self._instr
        if not RENDER_FLAGS <= self._sender_flags():          # an older show_sender: today's sound only
            rows = [r for r in rows if r.get("engine") == LIVE_ENGINE]
        if not rows:
            rows = [dict(LIVE_INSTRUMENT, engine=LIVE_ENGINE)]
        ids = [r["id"] for r in rows]
        if want in ids:
            return rows, want
        return rows, next((r["id"] for r in rows if r.get("engine") == LIVE_ENGINE), ids[0])

    def _outputs(self) -> list[dict]:
        """The places the piano can play (module docstring): OUTPUTS, plus a "sonos:<zone>" per sonos.json zone (re-read
        every RELOAD_S) while a rendered sound is offered and the piano script names SONOS_FLAGS."""
        out = [dict(o) for o in OUTPUTS]
        rows, _ = self._instruments()
        if not (RENDER_FLAGS | SONOS_FLAGS) <= self._sender_flags() or all(r.get("engine") == LIVE_ENGINE
                                                                           for r in rows):
            return out
        now = self.clock()
        if self._sonos is None or now - self._sonos_at >= contract.RELOAD_S:
            self._sonos, self._sonos_at = load_sonos_outputs(self.sonos_file), now
        return out + [dict(o) for o in self._sonos[0]]

    def _sender_flags(self) -> frozenset:
        """SENDER_FLAGS the test piano's script takes (re-read when the script changes)."""
        try:
            st = self.piano_script.stat()
            stamp: Any = (st.st_mtime_ns, st.st_size)
        except OSError:
            return frozenset()
        if self._flags[0] != stamp:
            self._flags = (stamp, sender_flags(self.piano_script))
        return self._flags[1]

    def _calibration_mic(self, source: str, output: str) -> tuple[Optional[str], str]:
        """calibration_mic() on show.yaml, sonos.json (read now: once per start) and the receiver's health."""
        rooms = sonos_rooms(self.sonos_file) if output.startswith(SONOS_PREFIX) else {}
        health = None
        if self.ctx.health is not None:
            try:
                health = self.ctx.health()
            except Exception:                                  # noqa: BLE001  show.yaml alone then
                health = None
        override = self._tests_cfg(source).get(CALIBRATION_KEY)
        if override is not None and not (isinstance(override, dict) or
                                         (isinstance(override, str) and set(override) <= NAME_OK)):
            log.warning("tests.%s.%s: %r isn't a stream name or a mapping (ignored)", source, CALIBRATION_KEY,
                        override)
            override = None
        return calibration_mic(self.settings, output, rooms=rooms, health=health, override=override)

    def _lead_ms(self, source: str) -> Optional[float]:
        """show.yaml ``tests.<source>.lead_ms`` (default LEAD_MS_DEFAULT, at most LEAD_MS_MAX); None when 0 or less."""
        v = _num_or(self._tests_cfg(source).get("lead_ms"), LEAD_MS_DEFAULT)
        return min(v, LEAD_MS_MAX) if v > 0 else None

    def _recording(self, piece: str) -> tuple[contract.PieceInfo, pathlib.Path]:
        """A ``rec:<id>`` piece: its row and .mid in recorder.py's folder. ValueError("unknown_piece")."""
        folder = recorder.recordings_folder(self.settings, self.recordings_dir)
        rid = piece[len(REC_PREFIX):]
        meta = recorder.read_meta(folder, rid)
        if meta is None:
            raise ValueError("unknown_piece")
        title = meta.get("title") if isinstance(meta.get("title"), str) and meta.get("title") else "Recording"
        return {"id": piece, "label": title, "kind": "recording"}, folder / f"{rid}.mid"

    def _library_piece(self, piece: str) -> tuple[contract.PieceInfo, pathlib.Path]:
        """A ``lib:``/``user:`` piece: its row (the library's title) and absolute .mid. ValueError("unknown_piece")."""
        path = library.resolve(piece)
        if path is None:
            raise ValueError("unknown_piece")
        title = library.piece_title(piece) or _label(path.stem)
        return {"id": piece, "label": title, "kind": piece.split(":", 1)[0]}, path

    def _folder(self, source: str) -> pathlib.Path:
        raw = self._tests_cfg(source).get(PLAYERS[source]["dir_key"])
        if not isinstance(raw, str) or not raw.strip():
            return pathlib.Path(PLAYERS[source]["default_dir"])
        p = pathlib.Path(raw.strip()).expanduser()
        return p if p.is_absolute() else contract.REPO_ROOT / p

    def _test_sender(self, source: str) -> tuple[Sender, str]:
        """The show.yaml sender this source plays as (else the contract's localhost default), and its stream."""
        player = PLAYERS[source]
        name = self._tests_cfg(source).get("sender") or player["default_sender"]
        yaml_senders, _ = parse_senders(self.settings)
        for s in yaml_senders:
            if s.name == name:
                return s, s.streams[0]
        stream = player["default_stream"]
        return (Sender(str(name), "localhost", player["default_port"], (stream,), hosts=("localhost",),
                       on_demand=True), stream)

    def _argv(self, c: _Child) -> list[str]:
        port = str(c.sender.port)
        if PLAYERS[c.source]["kind"] == "midi":
            piece = c.piece
            if piece.startswith("file:"):
                folder = self._folder(c.source)
                try:
                    same = folder.resolve() == contract.TEST_PIANO_DIR.resolve()
                except OSError:
                    same = False
                if not same:                                   # show_sender resolves file: ids in its own library
                    piece = str(folder / piece[5:])
            elif c.path is not None:                           # a recording: its absolute path
                piece = str(c.path)
            argv = [self.python, str(self.piano_script), "--test-piano", piece, "--port", port,
                    "--bind", LOCAL_BIND]
            if c.live:                                         # windows-gs + pc22: today's argv exactly
                if c.output == SILENT_OUTPUT:
                    argv.append("--no-speaker")
            elif c.instrument is not None:
                argv += ["--instrument", c.instrument, "--output", str(c.output or DEFAULT_OUTPUT),
                         "--status-file", str(c.status_path)]
                if str(c.output).startswith(SONOS_PREFIX) and "--title" in self._sender_flags():
                    argv += ["--title", c.label]
                if c.lead_ms is not None:
                    argv += ["--lead-ms", f"{c.lead_ms:g}"]
                if c.cal_mic is not None and CALIBRATION_FLAG in self._sender_flags():
                    argv += [CALIBRATION_FLAG, c.cal_mic]
            if c.live and c.status_path is not None:
                argv += ["--status-file", str(c.status_path)]
            if c.speed != 1.0:
                argv += ["--speed", f"{c.speed:g}"]
            if c.start_s > 0:
                argv += ["--start-s", f"{c.start_s:.3f}"]
            if c.once:
                argv.append("--once")
            return argv
        path = self._folder(c.source) / c.piece[5:]
        return [*self.audio_cmd, "--file", str(path), "--port", port, "--bind", LOCAL_BIND, "--stream", c.stream]

    def _spawn(self, c: _Child, now: float) -> None:
        argv = self._argv(c)
        c.log_path = self.log_dir / f"test-{c.source}.log"
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        if c.status_path is not None:                          # never read the last run's status
            try:
                c.status_path.unlink()
            except OSError:
                pass
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(c.log_path, "wb") as logf:               # truncated at each start
                logf.write((LOG_HEADER + " ".join(argv) + "\n").encode("utf-8", "replace"))
                logf.flush()
                run = [self.python, "-c", PDEATH_SHIM, str(os.getpid()), *argv] if self.pdeath else argv
                c.proc = self.popen(run, cwd=str(contract.REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
                                    stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        except OSError as e:
            c.error = f"couldn't start: {e}"
            c.set("failed", now)
            log.warning("test %s: %s", c.source, c.error)
            return
        c.started_s = now
        log.info("test %s: pid %d: %s", c.source, c.proc.pid, " ".join(argv))

    def _terminate(self, c: _Child, now: float) -> None:
        if c.proc is None or c.proc.poll() is not None or c.term_s is not None:
            return                                             # not running, or SIGTERM already sent
        try:
            c.proc.terminate()
        except OSError:
            pass
        c.term_s = now

    def _tail(self, c: _Child) -> str:
        try:
            with open(c.log_path, "rb") as f:                   # type: ignore[arg-type]
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 8192))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except (OSError, TypeError):
            lines = []
        lines = [ln for ln in lines if ln.strip() and not ln.startswith(LOG_HEADER)]
        return "\n".join(lines[-LOG_TAIL_LINES:])

    def _stream_live(self, c: _Child, now: float) -> bool:
        health = self.ctx.health
        if health is None:
            return c.started_s is not None and now - c.started_s >= NO_HEALTH_PLAYING_S
        try:
            h = health() or {}
            st = (h.get("streams") or {}).get(c.stream) or {}
        except Exception:                                      # noqa: BLE001
            return False
        if st.get("state") not in ("streaming", "quiet"):
            return False
        t_ms, age_ms = h.get("t_ms"), (st.get("net") or {}).get("age_ms")
        if isinstance(t_ms, (int, float)) and isinstance(age_ms, (int, float)) and c.started_s is not None:
            return (t_ms - age_ms) / 1000.0 >= c.started_s     # a packet arrived after this child started
        return True

    def _poll(self, now: float) -> None:
        before = self._running_key()
        for c in list(self._retiring):
            if c.proc.poll() is not None:
                self._retiring.remove(c)
            elif c.term_s is not None and now - c.term_s >= KILL_AFTER_S and not c.killed:
                _kill(c.proc)
                c.killed = True
        for source, c in self._cur.items():
            if c is None:
                continue
            if c.state == "starting" and c.proc is None:
                if not any(r.source == source for r in self._retiring):
                    self._spawn(c, now)
                continue
            if c.proc is None:
                continue
            rc = c.proc.poll()
            if c.status_path is not None and c.state in RUNNING:
                st = read_status(c.status_path)           # show_sender's stage, progress and error
                if st is not None:
                    c.stage, c.progress, c.status_error = st["stage"], st["progress"], st["error"]
                    c.pos_s, c.pos_at = st.get("pos_s"), st.get("pos_at")
            if c.state in RUNNING and rc is not None:
                if rc == 0 and c.once and c.stage != "error":
                    c.set("finished", now)                 # a once-only play ended by itself
                    log.info("test %s: finished", source)
                else:
                    c.error = c.status_error or self._tail(c) or f"exited with code {rc}"
                    c.set("failed", now)
                    log.warning("test %s: exited by itself (code %s)", source, rc)
            elif c.state in STARTING and c.stage == "preparing":
                c.set("preparing", now)                    # rendering the sound: no timeout, fast-polled a while
            elif c.state in STARTING and self._stream_live(c, now):
                c.set("playing", now)
            elif c.state == "preparing":
                c.set("starting", now)                     # rendered: now wait for its stream
            elif c.state == "stopping":
                if rc is not None:
                    c.set("off", now)
                elif c.term_s is not None and now - c.term_s >= KILL_AFTER_S and not c.killed:
                    _kill(c.proc)
                    c.killed = True
        if self._running_key() != before or self._claim_due():
            self._apply()                                  # (a claim or release that failed is tried again)
        self._rebuild()

    def _claim_wanted(self) -> Optional[str]:
        return next((c.claim for c in self._cur.values() if c is not None and c.state in RUNNING and c.claim), None)

    def _claim_due(self) -> bool:
        return self._mic_claim is not None and self._claim_wanted() != self._claim_applied

    def _running_key(self) -> tuple:
        return tuple(c.state in RUNNING if c is not None else False for c in self._cur.values())

    def _busy(self, now: float) -> bool:
        if self._retiring:
            return True
        for c in self._cur.values():
            if c is None:
                continue
            if c.state == "stopping":
                return True
            if c.state in STARTING and (c.proc is None or now - c.since_s < FAST_POLL_FOR_S):
                return True
        return False

    def _schedule(self) -> None:
        """Poll every FAST_POLL_S on show-brain while something is starting or stopping (only from that loop)."""
        loop = self.ctx.loop
        if loop is None or self._fast_handle is not None or not self._busy(self.clock()):
            return
        try:
            if asyncio.get_running_loop() is not loop:
                return
        except RuntimeError:
            return
        self._fast_handle = loop.call_later(FAST_POLL_S, self._fast)

    def _fast(self) -> None:
        self._fast_handle = None
        self._poll(self.clock())
        self._schedule()

    def _apply(self) -> None:
        """Hand the receiver the effective sender list and priority when they changed."""
        yaml_senders, self._sender_errors = parse_senders(self.settings)
        running_names, extra, prio = set(), [], []
        defined = {s.name for s in yaml_senders}
        for source, c in self._cur.items():
            if c is None or c.state not in RUNNING:
                continue
            running_names.add(c.sender.name)
            if c.sender.name not in defined and all(e.name != c.sender.name for e in extra):
                extra.append(c.sender)                         # show.yaml lacks it: the localhost default
            if self._tests_cfg(source).get("rank_first") is True and c.stream not in prio:
                prio.append(c.stream)
        eff = senders_from_settings(self.settings, tuple(self.ctx.static_senders or ()), running_names)
        eff = eff + tuple(e for e in extra if all(s.name != e.name for s in eff))
        errors = []
        if eff != self._applied:
            if self.ctx.set_senders is None:
                errors.append("the receiver can't take a sender list (set_senders missing): show.yaml senders "
                              "and test sources aren't subscribed")
            else:
                try:
                    self.ctx.set_senders(eff)
                    self._applied = eff
                    log.info("senders: %s", ", ".join(f"{s.name}={'|'.join(s.hosts or (s.host,))}:{s.port}"
                                                       for s in eff) or "none")
                except Exception as e:                         # noqa: BLE001
                    errors.append(f"set_senders failed: {type(e).__name__}: {e}")
        prio_t = tuple(prio)
        if prio_t != self._prio_applied:
            if self.ctx.set_priority is None:
                if prio_t:
                    errors.append("the receiver can't take a priority (set_priority missing)")
            else:
                try:
                    self.ctx.set_priority(prio_t)
                    self._prio_applied = prio_t
                except Exception as e:                         # noqa: BLE001
                    errors.append(f"set_priority failed: {type(e).__name__}: {e}")
        errors += self._apply_offsets()
        errors += self._apply_claim()
        self._apply_errors = errors
        self._wanted = (eff, prio_t)

    def _apply_claim(self) -> list[str]:
        """The calibration mic's claim (module docstring): held for the running Sonos run's mic, released when no
        running run needs it (a new stream replaces the old: one claim). Returns problems."""
        want = self._claim_wanted()
        if want == self._claim_applied:
            return []
        fn = self._mic_claim
        if fn is None:
            return [f"the mic can't be claimed (no seam): {want} listens only if something else turns it on"] \
                if want else []
        try:
            if want is not None:
                fn(want, True)
            else:
                fn(self._claim_applied, False)
            log.info("test piano: mic %s %s", want or self._claim_applied, "claimed" if want else "released")
            self._claim_applied = want
        except Exception as e:                                 # noqa: BLE001
            return [f"mic claim failed: {type(e).__name__}: {e}"]
        return []

    def _apply_offsets(self) -> list[str]:
        """The runtime acoustic offsets (module docstring): each running rendered run's lead on its stream; a stream
        whose run ended gets None (latency.json's own offset back). Returns problems."""
        want: dict[str, float] = {}
        for c in self._cur.values():
            if c is not None and c.state in RUNNING and c.lead_ms is not None:
                want[c.stream] = float(c.lead_ms)
        if want == self._offsets_applied or self._set_offset is None:
            return []
        problems = []
        for stream in sorted(set(self._offsets_applied) | set(want)):
            ms = want.get(stream)
            if self._offsets_applied.get(stream) == ms:
                continue
            try:
                self._set_offset(stream, ms)
                if ms is None:
                    self._offsets_applied.pop(stream, None)
                else:
                    self._offsets_applied[stream] = ms
                log.info("test piano: %s acoustic offset %s", stream, "removed" if ms is None else f"{ms:g} ms")
            except Exception as e:                             # noqa: BLE001
                problems.append(f"set_offset failed: {type(e).__name__}: {e}")
        return problems

    def _status(self, source: str, now: float) -> contract.SourceStatus:
        c = self._cur.get(source)
        if c is None:
            sender, stream = self._test_sender(source)
            midi = PLAYERS[source]["kind"] == "midi"
            return {"source": source, "state": "off", "piece": None, "label": None, "stream": stream,
                    "sender": sender.name, "pid": None, "since_s": None, "error": None,
                    "instrument": self._instruments()[1] if midi else None,
                    "output": DEFAULT_OUTPUT if midi else None, "once": False, "progress": None, "speed": 1.0,
                    "pos_s": None}
        error = c.error if c.state == "failed" else ((c.status_error or c.note) if c.state in RUNNING else None)
        return {"source": source, "state": c.state, "piece": c.piece, "label": c.label, "stream": c.stream,
                "sender": c.sender.name, "pid": c.proc.pid if c.proc is not None else None,
                "since_s": round(max(0.0, now - c.since_s), 3), "error": error,
                "instrument": c.instrument, "output": c.output, "once": c.once, "progress": c.progress,
                "speed": c.speed, "pos_s": self._pos_now(c)}

    @staticmethod
    def _pos_now(c: _Child) -> Optional[float]:
        """Where the piece is now: show_sender's last pos_s carried on to this moment at its speed (the score page's
        follow-along lights the bar playing, 23 Oct); as it was when no time came with it or it isn't playing."""
        if c.pos_s is None:
            return None
        if c.pos_at is None or c.state != "playing":
            return round(c.pos_s, 3)
        return round(c.pos_s + max(0.0, min(5.0, time.monotonic() - c.pos_at)) * c.speed, 3)

    def _rebuild(self) -> None:
        now = self.clock()
        yaml_senders, _ = parse_senders(self.settings)
        wanted = self._wanted
        subscribed = {s.name for s in (self._applied or ())}
        rows, seen = [], set()
        for s in list(wanted[0]) + yaml_senders:
            if s.name in seen:
                continue
            seen.add(s.name)
            rows.append({"name": s.name, "hosts": list(s.hosts or (s.host,)), "port": s.port,
                         "streams": list(s.streams), "on_demand": s.on_demand, "subscribed": s.name in subscribed})
        errors = list(self._sender_errors) + list(self._apply_errors)
        if _phrases_error:
            errors.append(_phrases_error)
        for source in PLAYERS:
            if self._tests_cfg(source) and not self._folder(source).is_dir():
                errors.append(f"tests.{source}.{PLAYERS[source]['dir_key']}: no folder {self._folder(source)}"
                              + (" (run tools/show/fetch_test_music.py)" if PLAYERS[source]["kind"] == "audio"
                                 else ""))
        if self._instr is not None:
            errors += self._instr[2]
        if self._sonos is not None:
            errors += self._sonos[1]
        cal = {src: {"output": c.output, "mic": c.cal_mic or None, "why": c.cal_why,
                     "claimed": c.claim is not None and c.claim == self._claim_applied}
               for src, c in self._cur.items() if c is not None and c.state in RUNNING and c.cal_mic is not None}
        self._snap = {"senders": rows, "tests": {s: self._status(s, now) for s in PLAYERS},
                      "priority": list(self._prio_applied or ()), "errors": errors,
                      "offsets_ms": dict(self._offsets_applied), "mic_claim": self._claim_applied,
                      "calibration": cal}


def attach(ctx: contract.BrainContext, **kw: Any) -> Sources:
    """The brain's ``sources``. Keyword args go to Sources (__main__ passes ``log_dir``)."""
    return Sources(ctx, **kw)


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m show_service.sources audio --file F --port P ...``: the test-audio child (test_audio.main)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["audio"]:
        from . import test_audio
        return test_audio.main(args[1:])
    print("usage: python -m show_service.sources audio --file F [--port P] [--bind 127.0.0.1] [--stream test-audio]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
