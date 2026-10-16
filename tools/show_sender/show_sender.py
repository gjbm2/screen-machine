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
              and ``devices``: every ALSA capture card's name (the Room page's Mic choices).  A SUBSCRIBE device hint
              for room-mic that names one of them exactly reopens capture on it (a new sess); an unknown name or
              none keeps the default.  No device: retried every 2 s, heartbeats say no_device.
  room-piano  USB MIDI from the ALSA raw-MIDI device of the card matching --midi-match (else the first card with a
              midiC*D0 node), re-found by name when the piano box power-cycles.  Every read's events go out at once
              as one batch (<= 32 events, ``prev`` = the last two batches, newest first); note on/off and CC
              64/66/67 on every channel are forwarded as they are (the adsilent's keyboard is channel 1, its own
              playback channel 2: show_rt decides).  A state packet every 500 ms: channel 1's keys down
              (88 strike velocities) and the last pedals; its seq is the newest batch sent.
  test-piano  --test-piano PIECE: a piece from config/live/test-piano/ (or a built-in phrase) played in real time as
              MIDI events and state, looping; no audio.  Needs the repo (stream_service/piano_replay.py).
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

    def on_packet(self, buf: bytes, addr: tuple, now: float) -> bool:
        pkt = vban.parse(buf)
        if pkt is None or not vban.is_subscribe(pkt):
            return False
        streams = vban.subscribed_streams(pkt) or []
        devices = vban.subscribed_devices(pkt)
        with self.lock:
            self.addr, self.streams, self.until, self.devices = addr, set(streams), now + SUB_TTL_S, devices
        return True

    def device(self, stream: str) -> Optional[str]:
        """The device the receiver last asked for on ``stream`` (the Room page's Mic choice), or None."""
        with self.lock:
            return self.devices.get(stream)

    def target(self, stream: str, now: float) -> Optional[tuple]:
        with self.lock:
            if self.addr is None or now > self.until or stream not in self.streams:
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


def run_test_piano(out: Out, stop: threading.Event, piece: str) -> None:
    """A piece from the test-piano library played in real time as test-piano MIDI, looping."""
    from stream_service.piano_replay import PHRASES, TestPiano, resolve
    piano = resolve(piece) if piece.startswith("file:") or piece in PHRASES else TestPiano.from_file(piece, loop=True)
    events = piano.midi()
    length = max(t for t, *_ in events) + 3.0
    ms = MidiStream("test-piano", out.send)
    log.info("test piano: %s (%d events, %.0f s)", piece, len(events), length)
    t0 = time.monotonic()
    i, last_state = 0, 0.0
    while not stop.is_set():
        now = time.monotonic()
        if i >= len(events):
            if now - t0 >= length:
                t0, i = now, 0
            else:
                stop.wait(0.01)
            continue
        due = []
        while i < len(events) and t0 + events[i][0] <= now:
            t_s, st, d1, d2 = events[i]
            due.append((now_ns() - int((now - t0 - t_s) * 1e9), st, d1, d2))
            i += 1
        if due:
            ms.events(due)
        if now - last_state >= STATE_S:
            ms.state(now_ns())
            last_state = now
        nxt = (t0 + events[i][0] - time.monotonic()) if i < len(events) else 0.01
        stop.wait(max(0.0005, min(0.01, nxt)))


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
        frame, n, t_cap = self.last if self.last else (None, None, None)
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
    """The USB mic through ``arecord`` -> room-mic (retried every 2 s; heartbeats meanwhile say no_device)."""
    au = AudioStream("room-mic", out.send, channels)
    period = au.frames
    in_lat_ms = 1000.0 * 4 * period / FS                  # arecord's buffer: 4 periods
    last_hb = 0.0
    node = "/dev/snd/pcmC{n}D0c"
    while not stop.is_set():
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
def main(argv: Optional[list] = None) -> None:
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
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = Out(a.port, a.bind)
    stop = threading.Event()
    threads = [threading.Thread(target=out.serve, args=(stop,), name="net", daemon=True)]
    if a.test_piano:
        threads.append(threading.Thread(target=run_test_piano, args=(out, stop, a.test_piano), name="test-piano",
                                        daemon=True))
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


if __name__ == "__main__":
    main()
