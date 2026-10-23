"""show-sender: the room mic and the Steinway's MIDI to show_service as VBAN (docs/SHOW_ENGINE.md §3.1-3.5).

    python3 show_sender.py [--port 6980] [--mic-match ATR] [--midi-match adsilent]       the kiosk unit
    python3 show_sender.py --test-piano "file:Jazz - Bill Evans - Here's That Rainy Day.mid" --port 6981
                                                                                         the test piano (PC22)

Receiver-initiated (§3.1): it binds UDP ``--port`` and streams only to the source address of the latest SUBSCRIBE
(show_rt sends one every second, listing the streams it wants); 5 s without one and it goes quiet.  Wire format,
counters, clocks and the sender rules are show_service/vban.py's (imported from the repo, or from a copy of vban.py
next to this file on the kiosk).  Clock: time.monotonic_ns() (Linux; one clock for heartbeats and MIDI).

Streams:
  room-mic    the USB mic (the first ALSA card whose name matches --mic-match, else the first USB capture card),
              captured by ``arecord`` (alsa-utils) at 48 kHz, mono, INT16, 128 frames a packet.  ``arecord``'s
              "overrun!!! (at least N ms long)" advances the counter and n over the lost packets (xruns +1); over
              250 ms lost starts a new sess (vban.py "Sender rules").  A heartbeat every 500 ms (its own TEXT
              counter): frame/n/t_cap_ns of the newest packet, levels over the last 500 ms, state ok / no_device,
              and ``devices``: every ALSA capture card's name (the Room page's Mic choices).  It captures only while
              the latest SUBSCRIBE lists room-mic: listed in its ``off``, arecord is closed and the heartbeats say
              off (null frame/n/t_cap_ns); in neither list, or no SUBSCRIBE for 5 s, nothing is opened or sent.  A SUBSCRIBE device hint
              for room-mic that names one of them exactly reopens capture on it (a new sess); an unknown name or
              none keeps the default.  No device: retried every 2 s, heartbeats say no_device.
  room-piano  USB MIDI from the ALSA raw-MIDI device of the card matching --midi-match (else the first card with a
              midiC*D0 node), re-found by name when the piano box power-cycles.  Every read's events go out at once
              as one batch (<= 32 events, ``prev`` = the last two batches, newest first); note on/off and CC
              64/66/67 on every channel are forwarded as they are (the adsilent's keyboard is channel 1, its own
              playback channel 2: show_rt decides).  A state packet every 500 ms: channel 1's keys down
              (88 strike velocities) and the last pedals; its seq is the newest batch sent.
  test-piano  --test-piano PIECE: a piece from config/live/test-piano/ (or a built-in phrase) played in real time as
              MIDI events and state, looping.  Needs the repo (stream_service/piano_replay.py).  On PC22 (WSL) it
              is also played out loud, in step, on Windows' built-in GS synth through the default speakers
              (pc22_synth.py, run by Windows Python through interop; --no-speaker: MIDI only), so PC22's mic hears
              it as the boundary mic will hear the Steinway.  Without Windows it is MIDI only.
              --once: the piece once, then exit 0 (default: loop, 3 s between passes).
              --instrument ID (not windows-gs) --output pc22: a rendered sound instead (the Room page's choice): the
              helper ``~/.venvs/sm-piano/bin/python -m piano_render.session`` (piano_render/session.py; its own
              session, stopped by "stop" + EOF on every exit path) loads the sound ("preparing": the load only,
              seconds) and renders the piece as it plays on PC22 (owner, 22 Oct: on demand, nothing stored); its
              "started" t_audio0 (when MIDI time 0 is heard: the sink's own report plus the output's measured
              delay) times the MIDI: each event is sent, and stamped, at t_audio0 + t_s - L (--lead-ms L:
              show_service holds the stream's notes L ms, so the screens match the sound).  An output heard later
              than L after "started" (a Sonos) simply gets its MIDI later, never early.  Its "rebase" (the mic heard
              the piano elsewhere: piano_render/calibrate.py; or the Sonos's own position) moves the MIDI clock.
              --pad-s = lead - first event + 0.1 (at least 0): the first event is never due sooner than 0.1 s after
              "started"; the helper plays that much silence first on PC22, less what the Sonos's start delay
              already gives on Sonos.  The .mid played is the library file itself, a recording's own path, or for
              a built-in phrase a .mid written from the same events.  --output none: MIDI only (no sound, no lead).
              --output sonos:<zone> (Play on Sonos, 22 Oct): the helper runs with --output sonos:<zone> and, once
              loaded, is sent "play-sonos {url, title}" each pass: url = http://<SM_LAN_HOST>:8700/show (the door
              the Sonos fetches the live stream through: SM_LAN_HOST from the repo's .env, else the environment;
              8700 = room_service's LAN door, whose /show/* reaches show_service's /api/v1/piano/stream/<id>.mp3);
              an older helper that still names a rendered file gets its .../api/v1/piano/audio/<key>.flac as before.
              title "Piano: <label>" (--title, else the piece's own name).  Its "warning" (e.g. "Sonos volume is 0")
              is logged and shown in the status's error while it plays.
              --status-file PATH: {"stage": preparing|playing|done|error, "progress", "error"} for show_service,
              written atomically, at least every second while preparing or playing.  SIGTERM: silence and exit 0.
"""
from __future__ import annotations

import argparse
import glob
import logging
import math
import os
import pathlib
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from typing import Callable, Optional

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
try:
    sys.path.insert(0, str(REPO))
    from show_service import vban                      # in the repo
except ImportError:                                    # on the kiosk: vban.py copied next to this file
    sys.path.insert(0, str(HERE))
    import vban                                        # type: ignore  # noqa: E402

import numpy as np  # noqa: E402

log = logging.getLogger("show_sender")

FS = 48000
SUB_TTL_S = 5.0
HEARTBEAT_S = 0.5
STATE_S = 0.5
NEW_SESS_LOST_S = 0.25
RETRY_S = 2.0
MIDI_PREV = 2
PEDALS = (64, 66, 67)
now_ns = time.monotonic_ns
# the test piano (PC22 only; nothing below runs or imports on the kiosk)
LOOP_GAP_S = 3.0                                  # between passes of a looping piece (after its last event)
ONCE_TAIL_S = 1.5                                 # --once on the GS synth: its release rings this long, then exit
LIVE_INSTRUMENT = "windows-gs"                    # today's sound: PC22's GS synth (Speaker)
OUTPUTS = ("pc22", "none")
SONOS_PREFIX = "sonos:"                           # --output sonos:<zone> (a zone of config/room/sonos.json)
ROOM_DOOR_PORT = 8700                             # room_service's LAN door: its /show/* reaches show_service
SHOW_DOOR = "/show"                               # ...under which show_service serves the live stream
PIANO_AUDIO_PATH = "/show/api/v1/piano/audio/{key}.flac"   # an older helper's rendered file
ENV_FILE = REPO / ".env"                          # SM_LAN_HOST: the address the Sonos fetches the sound from
TITLE_PREFIX = "Piano: "
SESSION_PY = "~/.venvs/sm-piano/bin/python"       # piano_render's venv
SESSION_CMD_ENV = "PIANO_SESSION_CMD"             # overrides the helper's command (tests)
PAD_EXTRA_S = 0.1                                 # the first event is never due sooner than this after "started"
PHRASE_MIDI_DIR = "~/.cache/screen-machine/piano/phrases"
SMF_TPQ = 10000                                   # with tempo 1 s a quarter: 0.1 ms ticks
STATUS_S = 0.5


def new_sess(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# -- subscriptions -------------------------------------------------------------------------------------------------
class Subscriptions:
    """The latest SUBSCRIBE: where to send, which streams, until when (any thread)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.addr: Optional[tuple] = None
        self.streams: set = set()
        self.until = 0.0
        self.devices: dict = {}                           # the receiver's device hints, {stream: name}
        self.off: set = set()                             # streams the receiver keeps closed (off heartbeats only)

    def on_packet(self, buf: bytes, addr: tuple, now: float) -> bool:
        pkt = vban.parse(buf)
        if pkt is None or not vban.is_subscribe(pkt):
            return False
        streams = vban.subscribed_streams(pkt) or []
        devices = vban.subscribed_devices(pkt)
        off = set(vban.subscribed_off(pkt)) - set(streams)
        with self.lock:
            self.addr, self.streams, self.until, self.devices = addr, set(streams), now + SUB_TTL_S, devices
            self.off = off
        return True

    def mode(self, stream: str, now: float) -> Optional[str]:
        """"on" (capture and send), "off" (device closed, off heartbeats) or None (nothing: no one asked)."""
        with self.lock:
            if self.addr is None or now > self.until:
                return None
            return "on" if stream in self.streams else "off" if stream in self.off else None

    def device(self, stream: str) -> Optional[str]:
        """The device the receiver last asked for on ``stream`` (the Room page's Mic choice), or None."""
        with self.lock:
            return self.devices.get(stream)

    def target(self, stream: str, now: float) -> Optional[tuple]:
        with self.lock:
            if self.addr is None or now > self.until or (stream not in self.streams and stream not in self.off):
                return None
            return self.addr


class Out:
    """One UDP socket: SUBSCRIBEs come in on it and everything goes out from it (its port is the sender's)."""

    def __init__(self, port: int, bind: str = "0.0.0.0") -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.subs = Subscriptions()
        self.sent = 0

    def send(self, stream: str, packet: bytes) -> bool:
        addr = self.subs.target(stream, time.monotonic())
        if addr is None:
            return False
        try:
            self.sock.sendto(packet, addr)
            self.sent += 1
            return True
        except OSError:
            return False

    def serve(self, stop: threading.Event) -> None:
        self.sock.settimeout(0.5)
        while not stop.is_set():
            try:
                buf, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.subs.on_packet(buf, addr, time.monotonic()):
                log.debug("subscribe from %s: %s", addr, sorted(self.subs.streams))


# -- MIDI ----------------------------------------------------------------------------------------------------------
class MidiParser:
    """Raw MIDI bytes -> [status, d1, d2] for note on/off and CC 64/66/67 (running status; realtime and SysEx skipped)."""

    def __init__(self) -> None:
        self.status = 0
        self.data: list[int] = []
        self.sysex = False

    def feed(self, data: bytes) -> list[tuple[int, int, int]]:
        out = []
        for b in data:
            if b >= 0xF8:                              # realtime: ignored, doesn't break running status
                continue
            if b == 0xF0:
                self.sysex = True
                continue
            if b == 0xF7:
                self.sysex = False
                continue
            if b & 0x80:
                self.sysex = False
                if b >= 0xF0:                          # system common: drop running status
                    self.status, self.data = 0, []
                    continue
                self.status, self.data = b, []
                continue
            if self.sysex or not self.status:
                continue
            self.data.append(b)
            kind = self.status & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            if len(self.data) < need:
                continue
            d1 = self.data[0]
            d2 = self.data[1] if need == 2 else 0
            self.data = []
            if kind == 0x90 and d2 == 0:
                out.append((0x80 | (self.status & 0x0F), d1, 0))
            elif kind in (0x80, 0x90) or (kind == 0xB0 and d1 in PEDALS):
                out.append((self.status, d1, d2))
        return out


class MidiStream:
    """Batches, the two previous batches, and the 500 ms state for one MIDI stream (one sess)."""

    def __init__(self, stream: str, send: Callable[[str, bytes], bool], sess: Optional[str] = None) -> None:
        self.stream, self.send = stream, send
        self.sess = sess or new_sess(stream)
        self.seq = 0
        self.counter = 0
        self.prev: list[tuple[int, list]] = []
        self.keys = [0] * 88
        self.cc = {64: 0, 66: 0, 67: 0}

    def events(self, ev: list[tuple[int, int, int, int]]) -> None:
        """[(t_ns, status, d1, d2)] read together: sent as batches of at most MIDI_BATCH_MAX."""
        for i in range(0, len(ev), vban.MIDI_BATCH_MAX):
            chunk = [list(e) for e in ev[i:i + vban.MIDI_BATCH_MAX]]
            for _t, st, d1, d2 in chunk:
                if st & 0x0F:
                    continue                           # the state is channel 1's (the keyboard)
                kind = st & 0xF0
                if kind in (0x80, 0x90) and 21 <= d1 <= 108:
                    self.keys[d1 - 21] = d2 if kind == 0x90 else 0
                elif kind == 0xB0 and d1 in self.cc:
                    self.cc[d1] = d2
            self.seq += 1
            prev = [[s, e] for s, e in reversed(self.prev)][:MIDI_PREV]          # newest first
            obj = vban.midi_events_obj(self.seq, chunk, prev=prev, sess=self.sess)
            self._send(vban.pack_midi_events(self.stream, self.counter, obj))
            self.prev = (self.prev + [(self.seq, chunk)])[-MIDI_PREV:]

    def state(self, t_ns: int) -> None:
        obj = vban.midi_state_obj(self.seq, list(self.keys), self.cc[64], self.cc[66], self.cc[67], t_ns,
                                  sess=self.sess)
        self._send(vban.pack_text(self.stream, self.counter, obj))

    def _send(self, packet: bytes) -> None:
        self.send(self.stream, packet)
        self.counter = (self.counter + 1) & 0xFFFFFFFF


def find_card(match: Optional[str], node_glob: str) -> Optional[tuple[int, str]]:
    """(card index, name) of the first ALSA card whose /proc/asound/cards line matches ``match`` (case-insensitive)
    and that has a node matching ``node_glob`` (e.g. /dev/snd/midiC{n}D0); without ``match``, any USB card."""
    try:
        text = pathlib.Path("/proc/asound/cards").read_text()
    except OSError:
        return None
    for m in re.finditer(r"^\s*(\d+)\s+\[([^\]]*)\]:\s*(.*)$", text, re.M):
        idx, cid, desc = int(m.group(1)), m.group(2).strip(), m.group(3)
        line = f"{cid} {desc}"
        if match and match.lower() not in line.lower():
            continue
        if not match and "usb" not in line.lower():
            continue
        if glob.glob(node_glob.format(n=idx)):
            return idx, cid
    return None


def _cards(node_glob: str) -> list[tuple[int, str]]:
    try:
        text = pathlib.Path("/proc/asound/cards").read_text()
    except OSError:
        return []
    return [(int(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^\s*(\d+)\s+\[([^\]]*)\]:", text, re.M)
            if glob.glob(node_glob.format(n=int(m.group(1))))]


def list_cards(node_glob: str) -> list[str]:
    """Names of the ALSA cards that have a node matching ``node_glob`` (the heartbeat's ``devices``)."""
    return [name for _, name in _cards(node_glob)]


def pick_card(hint: Optional[str], match: Optional[str], node_glob: str) -> Optional[tuple[int, str]]:
    """The receiver's hint when it names one of our cards exactly (SUBSCRIBE ``devices``), else ``match`` / the
    first USB card."""
    for card in _cards(node_glob) if hint else ():
        if card[1] == hint:
            return card
    return find_card(match, node_glob)


def run_rawmidi(out: Out, stop: threading.Event, match: Optional[str]) -> None:
    """The piano's USB MIDI -> room-piano; re-found by name when the box power-cycles."""
    ms: Optional[MidiStream] = None
    last_state = 0.0
    while not stop.is_set():
        card = find_card(match, "/dev/snd/midiC{n}D0")
        if card is None:
            stop.wait(RETRY_S)
            continue
        path = f"/dev/snd/midiC{card[0]}D0"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.warning("MIDI %s: %s", path, e)
            stop.wait(RETRY_S)
            continue
        log.info("MIDI: %s (%s)", path, card[1])
        ms = MidiStream("room-piano", out.send)              # a new sess per open (power cycle = new session)
        parser = MidiParser()
        try:
            while not stop.is_set():
                r, _, _ = select.select([fd], [], [], 0.05)
                t = now_ns()
                if r:
                    data = os.read(fd, 1024)
                    if not data:
                        break
                    ev = [(t, *e) for e in parser.feed(data)]
                    if ev:
                        ms.events(ev)
                if time.monotonic() - last_state >= STATE_S:
                    ms.state(now_ns())
                    last_state = time.monotonic()
        except OSError as e:
            log.warning("MIDI %s closed: %s", path, e)
        finally:
            os.close(fd)
        stop.wait(0.5)


class Speaker:
    """The test piano out loud on PC22: pc22_synth.py run by Windows Python through WSL interop, fed raw MIDI on its
    stdin. Nothing (a warning, MIDI only) when that can't start: off WSL, no Windows Python, no synth."""

    READY_S = 10.0

    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        py = shutil.which("py.exe") or "/mnt/c/Windows/py.exe"
        if not os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop") or not os.access(py, os.X_OK):
            log.warning("speaker: no Windows here (WSL interop, py.exe); MIDI only")
            return
        try:
            script = subprocess.run(["wslpath", "-w", str(HERE / "pc22_synth.py")], capture_output=True, text=True,
                                    check=True, timeout=5).stdout.strip()
            self.proc = subprocess.Popen([py, "-3", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("speaker: couldn't start pc22_synth: %s; MIDI only", e)
            return
        r, _, _ = select.select([self.proc.stdout], [], [], self.READY_S)
        line = self.proc.stdout.readline().decode("utf-8", "replace").strip() if r else "no answer"
        if not line.startswith("ready"):
            log.warning("speaker: pc22_synth: %s; MIDI only", line or "exited")
            self.close()
            return
        log.info("speaker: %s", line[5:].strip())

    def play(self, events: list) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.write(b"".join(bytes((st, d1, d2)) for _t, st, d1, d2 in events))
            self.proc.stdin.flush()
        except OSError as e:
            log.warning("speaker: pc22_synth gone (%s); MIDI only", e)
            self.close()

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()                         # end of input: it silences every note and exits
            self.proc.wait(timeout=2.0)
        except (OSError, subprocess.SubprocessError):
            self.proc.kill()
        self.proc = None


class TestStatus:
    """--status-file for show_service (sources.read_status): {"stage", "progress", "error"}, written atomically
    (tmp + rename) on every change and every STATUS_S while preparing or playing. No path: does nothing."""

    __test__ = False                                       # not a pytest class

    def __init__(self, path: Optional[str]) -> None:
        self.path = pathlib.Path(path) if path else None
        self.doc: Optional[dict] = None
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.thread: Optional[threading.Thread] = None

    @property
    def stage(self) -> Optional[str]:
        return self.doc["stage"] if self.doc else None

    def set(self, stage: str, progress: Optional[float] = None, error: Optional[str] = None) -> None:
        if self.path is None:
            return
        prog = None if progress is None else round(min(1.0, max(0.0, float(progress))), 4)
        with self.lock:
            self.doc = {"stage": stage, "progress": prog, "error": error}
            self._write()
        if self.thread is None and stage in ("preparing", "playing"):
            self.thread = threading.Thread(target=self._beat, name="status", daemon=True)
            self.thread.start()

    def _write(self) -> None:                              # under self.lock
        import json
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(self.doc), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.debug("status %s: %s", self.path, e)

    def _beat(self) -> None:
        while not self.halt.wait(STATUS_S):
            with self.lock:
                if self.doc and self.doc["stage"] in ("preparing", "playing"):
                    self._write()

    def close(self) -> None:
        self.halt.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)


def is_rendered(instrument: Optional[str]) -> bool:
    """--instrument names a rendered sound (absent or windows-gs: PC22's GS synth, today's)."""
    return bool(instrument) and instrument != LIVE_INSTRUMENT


def pad_for(events: list, lead_s: float) -> float:
    """--pad-s for the helper: silence before MIDI time 0, so the first event (sent lead_s before its sound) is due
    PAD_EXTRA_S after "started", never in the past."""
    return max(0.0, lead_s - events[0][0] + PAD_EXTRA_S)


def smf_bytes(events: list) -> bytes:
    """(t_s, status, d1, d2) events -> a type-0 standard MIDI file (tempo 1 s a quarter, SMF_TPQ: 0.1 ms ticks)."""
    import struct

    def varlen(v: int) -> bytes:
        out = [v & 0x7F]
        v >>= 7
        while v:
            out.append(0x80 | (v & 0x7F))
            v >>= 7
        return bytes(reversed(out))

    tr = bytearray(b"\x00\xff\x51\x03" + (1_000_000).to_bytes(3, "big"))
    last = 0
    for t_s, st, d1, d2 in events:
        tick = max(last, int(round(t_s * SMF_TPQ)))
        tr += varlen(tick - last) + bytes((st & 0xFF, d1 & 0x7F, d2 & 0x7F))
        last = tick
    tr += b"\x00\xff\x2f\x00"
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, SMF_TPQ) + b"MTrk" + struct.pack(">I", len(tr)) + bytes(tr)


def render_midi(piece: str, events: list) -> pathlib.Path:
    """The .mid the rendered sound is played from: the library file itself, a recording's own path, or for a built-in
    phrase a .mid written (once, by content) from the very events the MIDI loop sends."""
    from stream_service.piano_replay import LIBRARY_DIR, PHRASES
    if piece.startswith("file:"):
        return LIBRARY_DIR / piece[5:]
    if piece not in PHRASES:
        return pathlib.Path(piece).resolve()
    import hashlib
    data = smf_bytes(events)
    folder = pathlib.Path(PHRASE_MIDI_DIR).expanduser()
    path = folder / f"{piece}-{hashlib.sha256(data).hexdigest()[:12]}.mid"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return path


def session_argv(midi: pathlib.Path, instrument: str, pad_s: float, output: Optional[str] = None) -> list[str]:
    import shlex
    cmd = os.environ.get(SESSION_CMD_ENV)
    base = shlex.split(cmd) if cmd else [os.path.expanduser(SESSION_PY), "-m", "piano_render.session"]
    argv = [*base, "--midi", str(midi), "--instrument", instrument, "--pad-s", f"{pad_s:.3f}"]
    return argv + ["--output", output] if is_sonos(output) else argv


def is_sonos(output: Optional[str]) -> bool:
    return isinstance(output, str) and output.startswith(SONOS_PREFIX) and len(output) > len(SONOS_PREFIX)


def output_arg(v: str) -> str:
    """--output: pc22, none or sonos:<zone> (letters, digits, - _ .)."""
    if v in OUTPUTS or (is_sonos(v) and all(c.isalnum() or c in "-_." for c in v[len(SONOS_PREFIX):])):
        return v
    raise argparse.ArgumentTypeError(f"{v!r}: pc22, none or sonos:<zone>")


def lan_host() -> Optional[str]:
    """SM_LAN_HOST from the repo's .env (as tools/room/audio_cal/calibrate.py reads it), else the environment."""
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("SM_LAN_HOST="):
                v = line.split("=", 1)[1].strip().strip("\"'")
                if v:
                    return v
    except OSError:
        pass
    return os.environ.get("SM_LAN_HOST") or None


def sonos_url(rendered_path: Optional[str] = None, host: Optional[str] = None) -> str:
    """The URL for "play-sonos" (module docstring): the door the Sonos fetches the live stream through; for an older
    helper that names a rendered file, that file's URL. HelperFailed without a host, or for a file that isn't one."""
    key = None
    if rendered_path:
        key = pathlib.Path(str(rendered_path)).stem
        if not re.match(r"^[0-9a-f]{32}$", key):
            raise HelperFailed(f"the rendered file {pathlib.Path(str(rendered_path)).name} isn't one show_service "
                               "serves")
    host = host or lan_host()
    if not host:
        raise HelperFailed("no SM_LAN_HOST in .env (the address the Sonos fetches the sound from)")
    if key is None:
        return f"http://{host}:{ROOM_DOOR_PORT}{SHOW_DOOR}"
    return f"http://{host}:{ROOM_DOOR_PORT}{PIANO_AUDIO_PATH.format(key=key)}"


def piece_label(piece: str) -> str:
    """A piece's own name for the Sonos app: a library file's or a recording's stem, a phrase's label."""
    if not piece.startswith("file:"):
        try:
            from stream_service.piano_replay import PHRASE_LABELS
            if piece in PHRASE_LABELS:
                return str(PHRASE_LABELS[piece])
        except Exception:                                  # noqa: BLE001  the stem then
            pass
    stem = pathlib.Path(piece[5:] if piece.startswith("file:") else piece).stem
    text = " ".join(stem.replace("_", " ").split())
    return text[:1].upper() + text[1:]


class HelperFailed(Exception):
    """The rendered sound's helper said why it can't go on (or died): plain words for the Room page."""


class PianoSession:
    """The rendered sound's helper (piano_render/session.py): its JSON lines in a queue, commands on its stdin. It runs
    in a session of its own, so show_service's SIGKILL to our process group (2 s after its SIGTERM) can't cut it
    before the sound has stopped; it stops and exits by itself at EOF on its stdin (our exit, however it happens)."""

    EOF = "_eof"

    def __init__(self, argv: list[str]) -> None:
        import queue
        self._empty = queue.Empty
        self.q: "queue.Queue[dict]" = queue.Queue()
        self.proc = subprocess.Popen(argv, cwd=str(REPO), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     start_new_session=True)
        threading.Thread(target=self._read, name="piano-session", daemon=True).start()

    def _read(self) -> None:
        import json
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            try:
                msg = json.loads(line) if line else None
            except ValueError:
                log.warning("piano session: not a protocol line: %r", line[:200])
                continue
            if isinstance(msg, dict):
                self.q.put(msg)
        try:
            rc = self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            rc = None
        self.q.put({"ev": self.EOF, "rc": rc})

    def get(self, timeout: float) -> Optional[dict]:
        try:
            return self.q.get(timeout=timeout) if timeout > 0 else self.q.get_nowait()
        except self._empty:
            return None

    def send(self, cmd: str) -> bool:
        try:
            self.proc.stdin.write((cmd + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def close(self, timeout: float = 1.5) -> None:
        """"stop" and EOF (it silences and exits), then SIGTERM, then SIGKILL."""
        if self.proc.poll() is None:
            self.send("stop")
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        for wait_s, nxt in ((timeout, self.proc.terminate), (1.0, self.proc.kill), (1.0, None)):
            try:
                self.proc.wait(timeout=wait_s)
                return
            except subprocess.TimeoutExpired:
                if nxt is None:
                    log.error("piano session pid %d didn't exit after SIGKILL", self.proc.pid)
                    return
                log.warning("piano session pid %d still running: %s", self.proc.pid, nxt.__name__)
                nxt()


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _why(msg: dict) -> str:
    if msg.get("ev") == "error":
        return str(msg.get("why") or "the rendered piano failed")
    if msg.get("rc") in (-6, 134):                         # SIGABRT: the sample loader hit the stream's memory cap
        return "the rendered piano ran out of memory (its cap stops it before the box swaps)"
    return f"the rendered piano stopped (exit code {msg.get('rc')})"


class _Beat:
    """test-piano state packets every STATE_S."""

    def __init__(self, ms: MidiStream) -> None:
        self.ms, self.last = ms, 0.0

    def __call__(self, now: float) -> bool:
        if now - self.last < STATE_S:
            return False
        self.ms.state(now_ns())
        self.last = now
        return True


def run_test_piano(out: Out, stop: threading.Event, piece: str, speaker: bool = False, *,
                   instrument: Optional[str] = None, output: Optional[str] = None, status_file: Optional[str] = None,
                   lead_ms: Optional[float] = None, once: bool = False, title: Optional[str] = None) -> int:
    """A piece from the test-piano library played in real time as test-piano MIDI, looping (``once``: one pass);
    with ``speaker`` also out loud on PC22 (Speaker), so PC22's mic hears it as the boundary mic will hear the
    Steinway. A rendered ``instrument`` on output pc22 or sonos:<zone>: _run_rendered (``title``: the Sonos app's,
    after TITLE_PREFIX). Returns the exit code (1: it failed)."""
    status = TestStatus(status_file)
    try:
        from stream_service.piano_replay import PHRASES, TestPiano, resolve
        piano = (resolve(piece) if piece.startswith("file:") or piece in PHRASES
                 else TestPiano.from_file(piece, loop=True))
        events = piano.midi()
        length = max(t for t, *_ in events) + LOOP_GAP_S
        ms = MidiStream("test-piano", out.send)
        silent = output == "none" or (output is None and not speaker)        # --no-speaker: MIDI only too
        if is_rendered(instrument) and not silent:
            return _run_rendered(ms, stop, piece, events, length, instrument, status,
                                 max(0.0, float(lead_ms or 0.0)) / 1000.0, once, output=output,
                                 title=TITLE_PREFIX + (title or piece_label(piece)))
        if is_sonos(output):
            log.warning("test piano: the Windows synth plays on PC22 only: playing it there")
        if lead_ms:
            log.info("test piano: --lead-ms ignored (the sound isn't rendered on PC22)")
        spk = Speaker() if speaker and not silent and not is_rendered(instrument) else None
        try:
            status.set("playing", 0.0)
            _play_test_piano(ms, stop, piece, events, length, spk, once=once, status=status)
        finally:
            if spk is not None:
                spk.close()
        status.set("done", None if stop.is_set() else 1.0)
        return 0
    except Exception as e:                                 # noqa: BLE001  said in the status and the log; exit 1
        log.exception("test piano: %s", piece)
        status.set("error", None, f"test piano: {e}")
        return 1
    finally:
        status.close()


def _send_due(ms: MidiStream, events: list, i: int, t0: float, now: float, spk: Optional[Speaker] = None) -> int:
    """Send events[i:] due by ``now`` (event t_s is due at t0 + t_s), each stamped with its due time; the new i."""
    due = []
    while i < len(events) and t0 + events[i][0] <= now:
        t_s, st, d1, d2 = events[i]
        due.append((now_ns() - int((now - t0 - t_s) * 1e9), st, d1, d2))
        i += 1
    if due:
        ms.events(due)
        if spk is not None:
            spk.play(due)
    return i


def _play_test_piano(ms: MidiStream, stop: threading.Event, piece: str, events: list, length: float,
                     spk: Optional[Speaker], *, once: bool = False, status: Optional[TestStatus] = None) -> None:
    log.info("test piano: %s (%d events, %.0f s%s)", piece, len(events), length, ", once" if once else "")
    t0 = time.monotonic()
    end = max(t for t, *_ in events) + ONCE_TAIL_S
    i, last_state = 0, 0.0
    while not stop.is_set():
        now = time.monotonic()
        if i >= len(events):
            if once and now - t0 >= end:
                return
            if now - t0 >= length and not once:
                t0, i = now, 0
            else:
                stop.wait(0.01)
            continue
        i = _send_due(ms, events, i, t0, now, spk)
        if now - last_state >= STATE_S:
            ms.state(now_ns())
            last_state = now
            if status is not None:
                status.set("playing", (now - t0) / (end if once else length))
        nxt = (t0 + events[i][0] - time.monotonic()) if i < len(events) else 0.01
        stop.wait(max(0.0005, min(0.01, nxt)))


def _await(helper: PianoSession, stop: threading.Event, beat: _Beat, want: str,
           on_msg: Optional[Callable[[dict], None]] = None) -> Optional[dict]:
    """The helper's next ``want`` message (None on stop), state packets meanwhile; HelperFailed on error or exit."""
    while not stop.is_set():
        msg = helper.get(0.02)
        beat(time.monotonic())
        if msg is None:
            continue
        ev = msg.get("ev")
        if ev == want:
            return msg
        if ev in ("error", PianoSession.EOF):
            raise HelperFailed(_why(msg))
        if on_msg is not None:
            on_msg(msg)
    return None


def _run_rendered(ms: MidiStream, stop: threading.Event, piece: str, events: list, length: float,
                  instrument: str, status: TestStatus, lead_s: float, once: bool, output: Optional[str] = None,
                  title: Optional[str] = None) -> int:
    """The rendered sound on PC22 or a Sonos (module docstring): preparing while the helper loads the sound, then per
    pass "play" (Sonos: "play-sonos {url, title}") and the MIDI from its t_audio0 (each event sent at t_audio0 + t_s -
    lead_s; a "rebase" moves it); a pass ends when both the MIDI and the audio have; looping, the next play goes
    LOOP_GAP_S after the last event at the earliest."""
    import json
    sonos = is_sonos(output)
    pad_s = pad_for(events, lead_s)
    last_t = max(t for t, *_ in events)
    status.set("preparing", 0.0)
    try:
        midi = render_midi(piece, events)
        argv = session_argv(midi, instrument, pad_s, output)
        log.info("test piano: %s on %s (lead %.0f ms, pad %.3f s): %s", piece, instrument, lead_s * 1000, pad_s,
                 " ".join(argv))
        helper = PianoSession(argv)
    except OSError as e:
        why = (f"the rendered piano isn't installed here ({e.filename or e})" if isinstance(e, FileNotFoundError)
               else f"the rendered piano couldn't start: {e}")
        log.error("test piano: %s", why)
        status.set("error", None, why)
        return 1
    beat = _Beat(ms)
    note: list = [None]                                     # the helper's latest warning: the status's error

    def warned(m: dict) -> None:
        if m.get("ev") == "warning":
            note[0] = str(m.get("why") or "") or None
            log.warning("test piano: %s", note[0])
        elif m.get("ev") == "calibration":
            log.info("test piano: timing check: %s: %s", m.get("state"), m.get("why"))
    try:
        rendered = _await(helper, stop, beat, "rendered",
                          lambda m: status.set("preparing", m.get("frac")) if m.get("ev") == "progress" else None)
        if rendered is None:
            status.set("done")
            return 0
        duration = max(float(rendered.get("duration_s") or 0.0), last_t, 0.001)
        if rendered.get("streaming"):
            log.info("test piano: %s loaded in %.1f s: it renders as it plays (%.0f s of MIDI)", instrument,
                     float(rendered.get("load_s") or 0.0), duration)
        else:
            log.info("test piano: rendered %s (%.1f s, %s)", rendered.get("path"), duration,
                     "cached" if rendered.get("cached") else f"{float(rendered.get('render_s') or 0):.1f} s")
        play_cmd = "play"
        if sonos:
            play_cmd = "play-sonos " + json.dumps({"url": sonos_url(rendered.get("path")), "title": title})
            log.info("test piano: on %s: %s", output, play_cmd)
        while not stop.is_set():
            t_play = time.monotonic()
            helper.send(play_cmd)
            started = _await(helper, stop, beat, "started", warned)
            if started is None:
                break
            t_audio0 = float(started["t_audio0"])
            t0 = t_audio0 - lead_s
            now = time.monotonic()
            log.info("test piano: started %.0f ms after play; t_audio0 %.6f (in %+.0f ms), MIDI t0 %.6f (in %+.0f ms)"
                     ", pad %.2f s, measured delay %+.0f ms", (now - t_play) * 1000, t_audio0, (t_audio0 - now) * 1000,
                     t0, (t0 - now) * 1000, float(started.get("pad_s") or 0.0),
                     float(started.get("delay_ms") or 0.0))
            status.set("playing", 0.0, note[0])
            i, played = 0, False
            while not stop.is_set() and not (i >= len(events) and played):
                now = time.monotonic()
                i = _send_due(ms, events, i, t0, now)
                msg = helper.get(0)
                while msg is not None:
                    if msg.get("ev") == "played":
                        played = True
                        log.info("test piano: audio ended (%s)", {k: msg[k] for k in ("frames", "underruns", "by")
                                                                  if k in msg})
                    elif msg.get("ev") == "rebase" and _num(msg.get("t_audio0")) is not None:
                        was, t_audio0 = t_audio0, float(msg["t_audio0"])
                        t0 = t_audio0 - lead_s
                        log.info("test piano: rebased (%s): MIDI time 0 is heard %+.0f ms from the estimate",
                                 msg.get("by") or "sink", (t_audio0 - was) * 1000)
                    elif msg.get("ev") in ("warning", "calibration"):
                        warned(msg)
                    elif msg.get("ev") in ("error", PianoSession.EOF):
                        raise HelperFailed(_why(msg))
                    msg = helper.get(0)
                if beat(now):
                    status.set("playing", (now - t_audio0) / duration, note[0])
                nxt = (t0 + events[i][0] - time.monotonic()) if i < len(events) else 0.01
                stop.wait(max(0.0005, min(0.01, nxt)))
            if stop.is_set():
                break
            if once:
                status.set("done", 1.0)
                return 0
            status.set("playing", 1.0, note[0])
            while not stop.is_set() and time.monotonic() < t0 + length:
                msg = helper.get(0.01)
                if msg is not None and msg.get("ev") in ("error", PianoSession.EOF):
                    raise HelperFailed(_why(msg))
                beat(time.monotonic())
        status.set("done")
        return 0
    except HelperFailed as e:
        log.error("test piano: %s", e)
        status.set("error", None, str(e))
        return 1
    finally:
        helper.close()


# -- audio ---------------------------------------------------------------------------------------------------------
class AudioStream:
    """room-mic: 128-frame INT16 packets locked to the ADC (counter and n), overflows skipped over, heartbeats."""

    def __init__(self, stream: str, send: Callable[[str, bytes], bool], channels: int = 1, dev: str = "") -> None:
        self.stream, self.send, self.channels, self.dev = stream, send, channels, dev
        self.devices: Optional[list[str]] = None          # the capture choices the heartbeat lists
        self.frames = vban.frames_per_packet(channels, vban.INT16)
        self.restart_sess()
        self.text_counter = 0

    def restart_sess(self) -> None:
        self.sess = new_sess(self.stream)
        self.counter = 0
        self.n = 0
        self.xruns = 0
        self.clips = 0
        self.last = None                                  # (frame, n, t_cap_ns) of the newest packet
        self._peak = np.zeros(self.channels)
        self._sq = np.zeros(self.channels)
        self._cnt = 0

    def packet(self, pcm: np.ndarray, t_cap_ns: int) -> None:
        """One packet of ``frames`` x channels float32 (-1..1) whose first sample reached the ADC at t_cap_ns."""
        self.send(self.stream, vban.pack_audio(self.stream, self.counter, pcm, FS, vban.INT16))
        self.last = (self.counter, self.n, int(t_cap_ns))
        a = np.abs(pcm)
        self._peak = np.maximum(self._peak, a.max(axis=0))
        self._sq += (pcm.astype(np.float64) ** 2).sum(axis=0)
        self._cnt += pcm.shape[0]
        self.clips += int((a >= 0.99997).sum())
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        self.n += pcm.shape[0]

    def overflow(self, lost_s: float) -> None:
        """Capture time lost: skip the counter and n over it (no packets for it), or start a new sess."""
        if lost_s > NEW_SESS_LOST_S:
            self.restart_sess()
            return
        lost = max(1, int(round(lost_s * FS / self.frames)))
        self.counter = (self.counter + lost) & 0xFFFFFFFF
        self.n += lost * self.frames
        self.xruns += 1

    def heartbeat(self, state: str, in_lat_ms: float) -> None:
        def db(x):
            return [round(max(-120.0, 20.0 * math.log10(v)) if v > 0 else -120.0, 1) for v in x]
        rms = np.sqrt(self._sq / self._cnt) if self._cnt else np.zeros(self.channels)
        frame, n, t_cap = self.last if self.last and state != "off" else (None, None, None)
        obj = vban.make_heartbeat(sess=self.sess, frame=frame, n=n, t_cap_ns=t_cap, in_lat_ms=round(in_lat_ms, 2),
                                  dev=self.dev or "none", state=state, xruns=self.xruns,
                                  peak_dbfs=db(self._peak), rms_dbfs=db(rms), clips=self.clips,
                                  devices=self.devices)
        self.send(self.stream, vban.pack_text(self.stream, self.text_counter, obj))
        self.text_counter = (self.text_counter + 1) & 0xFFFFFFFF
        self._peak[:] = 0.0
        self._sq[:] = 0.0
        self._cnt = 0


OVERRUN = re.compile(r"overrun.*?at least\s+([\d.]+)\s*ms", re.I)


def run_arecord(out: Out, stop: threading.Event, match: Optional[str], channels: int = 1) -> None:
    """The USB mic through ``arecord`` -> room-mic, only while the receiver subscribes it (the owner: the mic is never
    on unless something needs it).  Kept off by the SUBSCRIBE: no arecord, heartbeats say off (and list the
    devices); asked for by nobody: nothing at all.  No device: retried every 2 s, heartbeats say no_device."""
    au = AudioStream("room-mic", out.send, channels)
    period = au.frames
    in_lat_ms = 1000.0 * 4 * period / FS                  # arecord's buffer: 4 periods
    last_hb = 0.0
    node = "/dev/snd/pcmC{n}D0c"
    while not stop.is_set():
        mode = out.subs.mode(au.stream, time.monotonic())
        if mode != "on":
            if mode == "off" and time.monotonic() - last_hb >= HEARTBEAT_S:
                au.devices = list_cards(node)
                au.heartbeat("off", 0.0)
                last_hb = time.monotonic()
            stop.wait(0.1)
            continue
        au.devices = list_cards(node)
        card = pick_card(out.subs.device(au.stream), match, node)
        if card is None or not _have("arecord"):
            if time.monotonic() - last_hb >= HEARTBEAT_S:
                au.dev = "none" if card is None else f"{card[1]} (arecord missing)"
                au.heartbeat("no_device", 0.0)
                last_hb = time.monotonic()
            stop.wait(0.5)
            continue
        au.dev = card[1]
        au.restart_sess()                                 # a device (re)open is a new sess
        cmd = ["arecord", "-q", "-D", f"plughw:CARD={card[1]},DEV=0", "-f", "S16_LE", "-r", str(FS), "-c",
               str(channels), "-t", "raw", f"--period-size={period}", f"--buffer-size={4 * period}", "-"]
        log.info("mic: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        lost: list[float] = []
        threading.Thread(target=_watch_stderr, args=(proc, lost), daemon=True).start()
        nbytes = period * channels * 2
        try:
            while not stop.is_set():
                if out.subs.mode(au.stream, time.monotonic()) != "on":
                    log.info("mic: off (not subscribed)")
                    break                                 # closes the device below
                buf = _read_exact(proc.stdout, nbytes)
                if buf is None:
                    break
                t_read = now_ns()
                while lost:
                    au.overflow(lost.pop(0) / 1000.0)
                pcm = np.frombuffer(buf, "<i2").reshape(-1, channels).astype(np.float32) / 32768.0
                au.packet(pcm, t_read - int((period / FS + in_lat_ms / 1000.0) * 1e9))
                if time.monotonic() - last_hb >= HEARTBEAT_S:
                    au.devices = list_cards(node)
                    au.heartbeat("xrun" if lost else "ok", in_lat_ms)
                    last_hb = time.monotonic()
                    want = out.subs.device(au.stream)
                    if want and want != card[1] and want in au.devices:
                        log.info("mic: the receiver asks for %s", want)
                        break                             # reopened on it below, as a new sess
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        want = out.subs.device(au.stream)
        if want and want != card[1] and want in au.devices:
            continue                                      # switching device: no retry wait
        if out.subs.mode(au.stream, time.monotonic()) != "on":
            continue                                      # turned off: no retry wait
        log.warning("mic: arecord stopped (code %s)", proc.poll())
        stop.wait(RETRY_S)


def _watch_stderr(proc: subprocess.Popen, lost: list) -> None:
    for line in iter(proc.stderr.readline, b""):
        m = OVERRUN.search(line.decode("utf-8", "replace"))
        if m:
            lost.append(float(m.group(1)))


def _read_exact(f, n: int) -> Optional[bytes]:
    buf = b""
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _have(cmd: str) -> bool:
    return any(os.access(os.path.join(p, cmd), os.X_OK) for p in os.environ.get("PATH", "").split(os.pathsep))


# -- main ----------------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=vban.PORT)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--mic-match", default=None, help="ALSA card name substring for the mic (default: first USB)")
    ap.add_argument("--midi-match", default=None, help="ALSA card name substring for the piano (default: first USB MIDI)")
    ap.add_argument("--channels", type=int, default=1)
    ap.add_argument("--no-mic", action="store_true")
    ap.add_argument("--no-midi", action="store_true")
    ap.add_argument("--test-piano", default=None, metavar="PIECE",
                    help="play a test-piano piece (file:<name>.mid from config/live/test-piano/, or a phrase)")
    ap.add_argument("--no-speaker", action="store_true",
                    help="test piano: MIDI only, not also out loud on PC22's speakers (pc22_synth.py)")
    ap.add_argument("--instrument", default=None, metavar="ID",
                    help="test piano: the sound (config/piano/instruments.json id); absent or windows-gs: PC22's GS "
                         "synth (today's); another: rendered by piano_render and played on --output")
    ap.add_argument("--output", default=None, type=output_arg, metavar="pc22|none|sonos:<zone>",
                    help="test piano: pc22 (PC22's speakers; the default), none (MIDI only, like --no-speaker) or "
                         "sonos:<zone> (a rendered sound on that Sonos zone, through room_service)")
    ap.add_argument("--title", default=None, metavar="TEXT",
                    help="test piano on Sonos: the piece's name for the Sonos app (after \"Piano: \")")
    ap.add_argument("--status-file", default=None, metavar="PATH",
                    help="test piano: keep {stage, progress, error} here for show_service")
    ap.add_argument("--lead-ms", type=float, default=None, metavar="L",
                    help="test piano, rendered on pc22: send each MIDI event L ms before its sound (show_service "
                         "holds the stream's notes that long)")
    ap.add_argument("--once", action="store_true", help="test piano: play the piece once, then exit 0")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: Optional[list] = None) -> Optional[int]:
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = Out(a.port, a.bind)
    stop = threading.Event()
    threads = [threading.Thread(target=out.serve, args=(stop,), name="net", daemon=True)]
    if a.test_piano:
        return _main_test_piano(a, out, stop, threads[0])
    else:
        if not a.no_midi:
            threads.append(threading.Thread(target=run_rawmidi, args=(out, stop, a.midi_match), name="midi",
                                            daemon=True))
        if not a.no_mic:
            threads.append(threading.Thread(target=run_arecord, args=(out, stop, a.mic_match, a.channels),
                                            name="mic", daemon=True))
    for t in threads:
        t.start()
    log.info("show-sender on udp %s:%d (%s)", a.bind, a.port, ", ".join(t.name for t in threads))
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return None


def _main_test_piano(a: argparse.Namespace, out: Out, stop: threading.Event, net: threading.Thread) -> int:
    """--test-piano: until the piece ends (--once), it fails, or SIGTERM/SIGHUP/Ctrl-C (silence, then exit 0)."""
    import signal
    rc = [0]

    def play() -> None:
        try:
            rc[0] = run_test_piano(out, stop, a.test_piano, not a.no_speaker, instrument=a.instrument,
                                   output=a.output, status_file=a.status_file, lead_ms=a.lead_ms, once=a.once,
                                   title=a.title)
        except BaseException:                              # noqa: BLE001
            log.exception("test piano")
            rc[0] = 1

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):                      # not the main thread (tests)
            pass
    th = threading.Thread(target=play, name="test-piano", daemon=True)
    net.start()
    th.start()
    log.info("show-sender on udp %s:%d (net, test-piano)", a.bind, a.port)
    try:
        while th.is_alive():
            th.join(0.5)
    except KeyboardInterrupt:
        stop.set()
        th.join(4.0)
    stop.set()
    return rc[0]


if __name__ == "__main__":
    sys.exit(main())
