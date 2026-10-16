"""A synthetic VBAN sender for show_service's end-to-end test and smoke runs: it behaves like the kiosk unit.

* It binds one UDP socket and waits for a SUBSCRIBE (receiver-initiated, vban.py). It streams to the source address
  of the latest SUBSCRIBE and stops 5 s after the last one (``vban.SUBSCRIBE_TTL_S``), as the kiosk unit does.
* **Audio** (``room-mic``, 48 kHz mono INT16, 128 frames a packet) is paced in real time from the first SUBSCRIBE.
  Packet ``i`` leaves when its last sample has been "captured". A heartbeat every 0.5 s carries
  ``frame``/``n``/``t_cap_ns`` on ``time.monotonic_ns()`` (the Linux sender clock), so the receiver's CaptureMap
  maps MIDI onto the audio timeline (§3.5).
* **MIDI** (``test-piano``): note and pedal events from a real ``.mid`` (config/live/test-piano/), sent as event
  batches when they fall due (``prev`` repeats the last two), then a state every 0.5 s with the keys held. Events are
  flushed before a state is taken (the state's ``seq`` is the last batch sent). One ``sess`` per run.
* :func:`drum_and_bass_loop` is the audio: kick on every beat, snare on 2 and 4, hats on the 8ths and 16ths, and a
  bass line on the 8ths, at 124 bpm by default.

Run by hand (a smoke test of a running show_service; nothing is sent until that service SUBSCRIBEs here)::

  ~/.venvs/sm-show/bin/python -m show_service.tests.synth_sender --bind 127.0.0.1:6990 --seconds 60
  # and start show_service with --sender synth=localhost:6990/room-mic,test-piano

Only ever bind 127.0.0.1 or an ephemeral port in tests.
"""

from __future__ import annotations

import argparse
import math
import pathlib
import socket
import threading
import time
import uuid
from typing import Optional, Sequence

import numpy as np

from show_service import contract, vban

FS = 48000
FRAMES = 128
BPM = 124.0
MID_DIR = contract.REPO_ROOT / "config" / "live" / "test-piano"
MID_FILE = MID_DIR / "Classical - Bach - Prelude and Fugue in F minor, WTC I.mid"   # MAESTRO v3, the shortest


# -- the audio --------------------------------------------------------------------------------------------------------
def _add(x: np.ndarray, i: int, s: np.ndarray) -> None:
    if i >= len(x):
        return
    e = min(len(x), i + len(s))
    x[i:e] += s[:e - i]


def drum_and_bass_loop(seconds: float = 30.0, bpm: float = BPM, fs: int = FS, seed: int = 7,
                       peak: float = 0.6) -> np.ndarray:
    """A drum-and-bass loop, float32 mono, ``peak`` full scale: kick on every beat (the beat times are
    ``k * 60 / bpm``), snare on 2 and 4, hats on the off-beat 8ths and the 16ths, and a bass line on the 8ths."""
    n = int(round(seconds * fs))
    x = np.zeros(n)
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm

    m = np.arange(int(0.18 * fs))
    f = 48.0 + 85.0 * np.exp(-m / (0.025 * fs))
    kick = np.exp(-m / (0.06 * fs)) * np.sin(2 * np.pi * np.cumsum(f) / fs)
    c = int(0.003 * fs)
    kick[:c] += 0.5 * rng.standard_normal(c) * np.exp(-np.arange(c) / (0.001 * fs))
    m = np.arange(int(0.14 * fs))
    snare = np.exp(-m / (0.035 * fs)) * (0.55 * np.diff(rng.standard_normal(len(m) + 1))
                                         + 0.45 * np.sin(2 * np.pi * 190.0 * m / fs))
    m = np.arange(int(0.035 * fs))
    hat = np.exp(-m / (0.007 * fs)) * np.diff(rng.standard_normal(len(m) + 1))
    m = np.arange(int(0.75 * beat * fs) // 2)
    env = np.minimum(1.0, m / (0.004 * fs)) * np.exp(-m / (0.09 * fs))
    notes = (55.0, 55.0, 82.41, 55.0, 65.41, 55.0, 49.0, 73.42)     # A1 ... per 8th, one bar
    basses = [env * (np.sin(2 * np.pi * f0 * m / fs) + 0.35 * np.sin(4 * np.pi * f0 * m / fs)) for f0 in notes]

    for k in range(int(seconds / beat) + 1):
        i = int(round(k * beat * fs))
        _add(x, i, 0.9 * kick)
        if k % 4 in (1, 3):
            _add(x, i, 0.45 * snare)
        for j, amp in ((1, 0.08), (2, 0.18), (3, 0.08)):
            _add(x, i + int(round(j * beat / 4 * fs)), amp * hat)
        for j in (0, 1):
            _add(x, i + int(round(j * beat / 2 * fs)), 0.3 * basses[(2 * k + j) % len(basses)])
    x += 1e-3 * rng.standard_normal(n)
    return (x * (peak / np.max(np.abs(x)))).astype(np.float32)


# -- the MIDI ---------------------------------------------------------------------------------------------------------
def midi_events(path: pathlib.Path | str = MID_FILE, seconds: float = 30.0) -> list[tuple[float, int, int, int]]:
    """``(t_s, status, d1, d2)`` from a .mid (mido, pinned): note on/off and control changes in the first
    ``seconds``, times from the file's start."""
    import mido
    out: list[tuple[float, int, int, int]] = []
    t = 0.0
    for msg in mido.MidiFile(str(path)):
        t += msg.time
        if t >= seconds:
            break
        if msg.type == "note_on":
            out.append((t, 0x90 | msg.channel, msg.note, msg.velocity))
        elif msg.type == "note_off":
            out.append((t, 0x80 | msg.channel, msg.note, msg.velocity))
        elif msg.type == "control_change":
            out.append((t, 0xB0 | msg.channel, msg.control, msg.value))
    return out


# -- the sender -------------------------------------------------------------------------------------------------------
class SynthSender:
    """A receiver-initiated VBAN sender on its own thread ("synth-sender"). ``start()``, then ``stop()``."""

    def __init__(self, audio: Optional[np.ndarray] = None, events: Sequence[tuple[float, int, int, int]] = (), *,
                 bind: tuple[str, int] = ("127.0.0.1", 0), audio_stream: str = "room-mic",
                 midi_stream: str = "test-piano", fs: int = FS, frames: int = FRAMES) -> None:
        self.audio = None if audio is None else np.asarray(audio, np.float32).reshape(-1, 1)
        self.events = list(events)
        self.audio_stream, self.midi_stream = audio_stream, midi_stream
        self.fs, self.frames = fs, frames
        self.sess = f"synth-{uuid.uuid4().hex[:8]}"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(bind)
        self.port = self.sock.getsockname()[1]
        self.dest: Optional[tuple[str, int]] = None
        self.wanted: list[str] = []
        self.subscribes = 0
        self.sent = {"audio": 0, "heartbeat": 0, "batches": 0, "states": 0, "events": 0, "errors": 0}
        self.t_start: Optional[float] = None
        self.t_start_ns: Optional[int] = None
        self.done = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._packets: list[bytes] = []
        if self.audio is not None:                       # pre-packed: the sender stays light in a shared process
            n = len(self.audio) // frames
            self._packets = [vban.pack_audio(audio_stream, i, self.audio[i * frames:(i + 1) * frames], fs)
                             for i in range(n)]

    @property
    def duration_s(self) -> float:
        a = len(self._packets) * self.frames / self.fs
        e = self.events[-1][0] if self.events else 0.0
        return max(a, e)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="synth-sender", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.sock.close()

    # -- the loop ----------------------------------------------------------------------------------------------------
    def _send(self, buf: bytes, kind: str) -> None:
        if self.dest is None:
            return
        try:
            self.sock.sendto(buf, self.dest)
            self.sent[kind] += 1
        except OSError:                                  # nobody listening (ICMP refused): carry on
            self.sent["errors"] += 1

    def _poll_subscribe(self, wait: float) -> None:
        self.sock.settimeout(max(0.0005, wait))
        try:
            data, src = self.sock.recvfrom(2048)
        except (socket.timeout, BlockingIOError, InterruptedError):
            return
        except OSError:
            self._stop.wait(wait)
            return
        pkt = vban.parse(data)
        streams = vban.subscribed_streams(pkt) if isinstance(pkt, vban.TextPacket) else None
        if streams is not None:
            self.dest, self.wanted, self.subscribes = src, streams, self.subscribes + 1
            self._last_sub = time.monotonic()

    def _run(self) -> None:
        fr, fs = self.frames, self.fs
        self._last_sub = -math.inf
        while not self._stop.is_set() and self.dest is None:      # receiver-initiated: wait for a SUBSCRIBE
            self._poll_subscribe(0.05)
        if self._stop.is_set():
            return
        t0 = self.t_start = time.monotonic()
        t0_ns = self.t_start_ns = int(round(t0 * 1e9))
        i = 0                                                      # next audio packet
        hb_c = midi_c = 0                                          # TEXT counters (per stream name)
        next_hb = t0
        ev_i, seq = 0, 0
        prev: list[tuple[int, list]] = []
        keys = [0] * vban.PIANO_KEYS
        cc = {64: 0, 66: 0, 67: 0}
        next_state = t0 + vban.MIDI_STATE_EVERY_S
        end = t0 + self.duration_s + 0.05
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= end:
                break
            live = now - self._last_sub <= vban.SUBSCRIBE_TTL_S    # nobody asking for 5 s: capture on, send nothing
            audio_on = live and self.audio_stream in self.wanted
            midi_on = live and self.midi_stream in self.wanted
            # audio: every packet whose last sample is captured by now
            while i < len(self._packets) and t0 + (i + 1) * fr / fs <= now:
                if audio_on:
                    self._send(self._packets[i], "audio")
                i += 1
            if audio_on and now >= next_hb and i > 0:
                n = (i - 1) * fr                                   # the last packet sent: its first sample
                seg = self.audio[max(0, n + fr - fs // 2):n + fr, 0]
                rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))) if len(seg) else 0.0
                pk = float(np.max(np.abs(seg))) if len(seg) else 0.0
                hb = vban.make_heartbeat(sess=self.sess, frame=i - 1, n=n, t_cap_ns=t0_ns + round(n * 1e9 / fs),
                                         in_lat_ms=0.0, dev="synth", state="ok", xruns=0,
                                         peak_dbfs=[20 * math.log10(max(pk, 1e-9))],
                                         rms_dbfs=[20 * math.log10(max(rms, 1e-9))])
                self._send(vban.pack_text(self.audio_stream, hb_c, hb), "heartbeat")
                hb_c += 1
                next_hb += vban.HEARTBEAT_EVERY_S
            # MIDI: due events in batches of <= 32, then (after them) a state when due
            due = []
            while ev_i < len(self.events) and t0 + self.events[ev_i][0] <= now:
                t, st, d1, d2 = self.events[ev_i]
                due.append([t0_ns + int(round(t * 1e9)), st, d1, d2])
                kind = st & 0xF0
                if kind == 0x90 and d2 > 0 and vban.PIANO_LOW <= d1 < vban.PIANO_LOW + vban.PIANO_KEYS:
                    keys[d1 - vban.PIANO_LOW] = d2
                elif kind in (0x80, 0x90) and vban.PIANO_LOW <= d1 < vban.PIANO_LOW + vban.PIANO_KEYS:
                    keys[d1 - vban.PIANO_LOW] = 0
                elif kind == 0xB0 and d1 in cc:
                    cc[d1] = d2
                ev_i += 1
            for b0 in range(0, len(due), vban.MIDI_BATCH_MAX):
                batch = due[b0:b0 + vban.MIDI_BATCH_MAX]
                seq += 1
                if midi_on:
                    obj = vban.midi_events_obj(seq, batch, prev, sess=self.sess)
                    self._send(vban.pack_midi_events(self.midi_stream, midi_c, obj), "batches")
                    midi_c += 1
                    self.sent["events"] += len(batch)
                prev = [(seq, batch)] + prev[:vban.MIDI_PREV - 1]
            if now >= next_state:
                next_state += vban.MIDI_STATE_EVERY_S
                if midi_on and self.events:
                    obj = vban.midi_state_obj(seq, keys, cc[64], cc[66], cc[67], time.monotonic_ns(), sess=self.sess)
                    self._send(vban.pack_text(self.midi_stream, midi_c, obj), "states")
                    midi_c += 1
            # sleep (listening for SUBSCRIBEs) until the next packet is due, at most 5 ms
            nxt = t0 + (i + 1) * fr / fs if i < len(self._packets) else now + 0.005
            self._poll_subscribe(min(0.005, max(0.0, nxt - time.monotonic())))
        self.done.set()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m show_service.tests.synth_sender",
                                 description="a synthetic VBAN sender (room-mic loop + test-piano MIDI)")
    ap.add_argument("--bind", default="127.0.0.1:6990", help="HOST:PORT to listen for SUBSCRIBE on")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--bpm", type=float, default=BPM)
    ap.add_argument("--no-midi", action="store_true")
    a = ap.parse_args(argv)
    host, _, port = a.bind.rpartition(":")
    snd = SynthSender(drum_and_bass_loop(a.seconds, a.bpm), () if a.no_midi else midi_events(MID_FILE, a.seconds),
                      bind=(host or "127.0.0.1", int(port)))
    print(f"synth sender on udp {host}:{snd.port}: waiting for a SUBSCRIBE", flush=True)
    snd.start()
    try:
        while not snd.done.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    snd.stop()
    print(f"sent {snd.sent}, subscribes {snd.subscribes}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
