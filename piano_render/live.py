"""Play what the room plays, live, as an endless MP3 (owner, 23 Oct: "synth on PC22 and then stream to local device
browser": the Room page's "This device" output, a phone or iPad playing PC22's own piano sound).

    python -m piano_render.live [--instrument ID] [--room ROOM|none] [--bitrate 128] [--format mp3|pcm]
        stdin:  one JSON object a line, a MIDI event at its sound time on the host's CLOCK_MONOTONIC:
                {"t": seconds, "s": status, "a": data1, "b": data2}  (note on / off, CC 64 / 66 / 67; others ignored)
                {"panic": true}  every note and pedal off
        stdout: MP3 (ffmpeg's libmp3lame, 48 kHz stereo, --bitrate kb/s CBR), for as long as stdin stays open;
                --format pcm: raw s16le, 48 kHz stereo interleaved (frame n is stream second n / 48000), no encoder
        stderr: one JSON line once loaded ({"ready": true, ...}), warnings, then the stats at the end

The instrument is a live engine: sfizz in live mode (partial preload, engines/sfizz.py) or FluidSynth, one block at a
time.  Events go into the block that plays at their sound time + DELAY_S (the network's and the bus's jitter
absorbed, so the rhythm is the player's, not the packets'); late ones into the next block.  The chain is the parts
of the realism chain that need no knowledge of the future: the instrument's EQ and width, its room (partitioned
convolution) and the master (calibrated gain, true-peak limiter).  The resonance bank and una corda read the whole
piece ahead, so a live stream goes without them.  Rendering is paced to the wall clock, AHEAD_S ahead; after
IDLE_S of silence with no voice sounding it writes zeros without running the engine or the chain.  Everything
else (fan-out to browsers, starting and stopping) is show_service's piano_live.py.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import queue
import subprocess
import sys
import threading
import time
from typing import Optional

import numpy as np

from . import engines as _engines
from . import instruments as _instruments
from .score import Event

log = logging.getLogger("piano_render.live")

SR = 48000
DELAY_S = 0.35                       # an event plays this long after its sound time (0.25 left 0.13 s for the bus)
LATE_DROP_S = 0.2                    # a note on this late is dropped (a burst of stale notes is worse than a gap)
AHEAD_S = 0.12                       # rendered this far ahead of the wall clock
IDLE_S = 4.0                         # silence (no voice, no event) this long: zeros without the engine or chain
NICE = 5
PEDALS = (64, 66, 67)


def _chain(reg, inst: dict, room_arg: Optional[str]):
    """The realism chain's live parts: tone (EQ, width), the room, the master."""
    from . import render as _render
    from .dsp import streaming as S
    stages: list = [S.ToneStage(inst, SR, None, una_corda=False)]
    room_id = _render.resolve_room(reg, inst, room_arg, True)
    room = reg.room(room_id) if room_id else None
    if room and room.get("wet_db") is not None:
        try:
            stages.append(S.RoomStage(room, SR))
        except Exception as e:                           # noqa: BLE001  a missing IR: dry, still playing
            log.warning("room %s skipped: %s", room_id, e)
            room_id = None
    stages.append(S.MasterStage(SR, float(inst.get("gain_db") or 0.0), limiter=True))
    return S.Chain(stages), room_id


class Live:
    def __init__(self, instrument: Optional[str] = None, room: Optional[str] = None, bitrate: int = 128,
                 out=None, clock=time.monotonic, sleep=time.sleep, fmt: str = "mp3") -> None:
        self.fmt = fmt
        self.reg = _instruments.load()
        inst_id = instrument if instrument and instrument in self.reg.ids() else self.reg.default
        inst = self.reg.get(inst_id)
        if inst.get("engine") in _engines.NEVER_RENDERED:
            inst = self.reg.get(self.reg.default)
        self.inst = inst
        self.room_arg = room
        self.bitrate = int(bitrate)
        self.out = out if out is not None else sys.stdout.buffer
        self.clock, self.sleep = clock, sleep
        self.q: "queue.SimpleQueue" = queue.SimpleQueue()
        self.stats: dict = {"instrument": inst.get("id"), "events": 0, "late": 0, "dropped": 0, "blocks": 0,
                            "idle_blocks": 0}
        self._stop = threading.Event()

    # -- input ----------------------------------------------------------------------------------------------------
    def feed(self, line: str) -> None:
        try:
            m = json.loads(line)
        except ValueError:
            return
        if isinstance(m, dict):
            self.q.put(m)

    def _reader(self, f) -> None:
        for line in f:
            self.feed(line)
        self._stop.set()

    # -- the loop -------------------------------------------------------------------------------------------------
    def open(self) -> dict:
        t0 = time.monotonic()
        e = _engines.create(self.inst, self.reg.engines_root)
        e.realtime = True
        e.open(SR)
        e._reset()
        e._set_pitch(0.0)
        self.eng = e
        self.chain, self.room = _chain(self.reg, self.inst, self.room_arg)
        self.stats.update(engine=self.inst.get("engine"), room=self.room, load_s=round(time.monotonic() - t0, 2),
                          block=e.block)
        return self.stats

    def _encoder(self) -> subprocess.Popen:
        return subprocess.Popen(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2",
             "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k", "-f", "mp3", "-flush_packets", "1",
             "pipe:1"], stdin=subprocess.PIPE, stdout=self.out, bufsize=0)

    def run(self, stdin=None) -> dict:
        e = self.eng
        B = e.block
        enc = self._encoder() if self.fmt == "mp3" else None
        sink = enc.stdin if enc is not None else self.out
        if stdin is not None:
            threading.Thread(target=self._reader, args=(stdin,), daemon=True, name="live-in").start()
        half = e.half_pedal
        sent = {64: 0, 66: 0, 67: 0}
        pending: list = []                               # (frame, Event)
        out_l = np.zeros(B, np.float32)
        out_r = np.zeros(B, np.float32)
        zeros = np.zeros((B, 2), np.float32)
        t0 = self.clock()
        # the stream's clock, for a listener matching what it hears to the screens and the score: audio at stream
        # second x carries the notes whose sound time is t0 + x - DELAY_S (host CLOCK_MONOTONIC)
        sys.stderr.write(json.dumps({"clock": {"t0": round(t0, 4), "delay_s": DELAY_S, "sr": SR}}) + "\n")
        sys.stderr.flush()
        frame = 0
        last_sound = 0
        idle = False
        try:
            while not self._stop.is_set():
                ahead = frame / SR - (self.clock() - t0)
                if ahead > AHEAD_S:
                    self.sleep(min(0.02, ahead - AHEAD_S))
                    continue
                while True:                              # the events that arrived
                    try:
                        m = self.q.get_nowait()
                    except queue.Empty:
                        break
                    if m.get("panic") or _all_off(m):
                        pending.clear()
                        pending.extend((frame, Event(0.0, "off", k, 0)) for k in range(21, 109))
                        pending.extend((frame, Event(0.0, "cc", c, 0)) for c in PEDALS)
                        continue
                    ev = self._event(m)
                    if ev is None:
                        continue
                    t = m.get("t")
                    f = frame if not isinstance(t, (int, float)) or not math.isfinite(t) else \
                        int(round((float(t) + DELAY_S - t0) * SR))
                    if f < frame:
                        self.stats["late"] += 1
                        if ev.kind == "on" and frame - f > LATE_DROP_S * SR:
                            self.stats["dropped"] += 1   # stale (it arrived while the sound loaded): not played
                            continue
                        f = frame
                    pending.append((f, ev))
                    self.stats["events"] += 1
                pending.sort(key=lambda x: x[0])
                batch = []
                while pending and pending[0][0] < frame + B:
                    f, ev = pending.pop(0)
                    if ev.kind == "cc" and ev.num in sent and not (half and ev.num == 64):
                        v = 127 if ev.value >= 64 else 0
                        if v == sent[ev.num]:
                            continue
                        sent[ev.num] = v
                        ev = Event(0.0, "cc", ev.num, v)
                    elif ev.kind == "cc":
                        sent[ev.num] = ev.value
                    batch.append((f - frame, ev, e.velocity01(ev.value) if ev.kind == "on" else 0.0))
                if batch:
                    last_sound = frame
                    idle = False
                if idle:
                    y = zeros
                    self.stats["idle_blocks"] += 1
                else:
                    e._render_block(out_l, out_r, batch)
                    y = self.chain.push(np.stack([out_l, out_r], axis=1))
                    if e._active_voices() > 0:
                        last_sound = frame
                    elif frame - last_sound > IDLE_S * SR and not any(sent.values()):
                        idle = True                      # the chain's tail has long died away
                if y.shape[0]:
                    if enc is not None:
                        sink.write(np.ascontiguousarray(y, np.float32).tobytes())
                    else:
                        sink.write((np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
                        sink.flush()
                frame += B
                self.stats["blocks"] += 1
        except (BrokenPipeError, OSError) as ex:         # the listener went away
            self.stats["ended"] = f"{type(ex).__name__}"
        finally:
            if enc is not None:
                try:
                    enc.stdin.close()
                except OSError:
                    pass
                try:
                    enc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    enc.kill()
            self.stats["audio_s"] = round(frame / SR, 1)
        return self.stats

    @staticmethod
    def _event(m: dict) -> Optional[Event]:
        try:
            st, a, b = int(m["s"]), int(m["a"]), int(m.get("b", 0))
        except (KeyError, TypeError, ValueError):
            return None
        kind = st & 0xF0
        if kind == 0x90 and b > 0 and 21 <= a <= 108:
            return Event(0.0, "on", a, b)
        if kind in (0x80, 0x90) and 21 <= a <= 108:
            return Event(0.0, "off", a, b if kind == 0x80 else 0)
        if kind == 0xB0 and a in PEDALS:
            return Event(0.0, "cc", a, b)
        return None


def _all_off(m: dict) -> bool:
    """CC 120 (all sound off) or 123 (all notes off): everything let go at once, as a panic."""
    try:
        return int(m.get("s", 0)) & 0xF0 == 0xB0 and int(m.get("a", -1)) in (120, 123)
    except (TypeError, ValueError):
        return False


def main(argv: Optional[list] = None) -> int:
    from .stream import prepare_process
    ap = argparse.ArgumentParser(prog="python -m piano_render.live")
    ap.add_argument("--instrument")
    ap.add_argument("--room")
    ap.add_argument("--bitrate", type=int, default=128)
    ap.add_argument("--format", choices=("mp3", "pcm"), default="mp3")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    proc = prepare_process(nice=NICE)
    live = Live(a.instrument, a.room, a.bitrate, fmt=a.format)
    st = live.open()
    sys.stderr.write(json.dumps({"ready": True, **st, **proc}) + "\n")
    sys.stderr.flush()
    stats = live.run(sys.stdin)
    sys.stderr.write(json.dumps({"stats": stats}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
