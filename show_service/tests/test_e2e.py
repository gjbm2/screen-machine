"""End to end: every real module wired by ``__main__.Show``, in-process, on ephemeral 127.0.0.1 ports only.

A synthetic VBAN sender (tests/synth_sender.py) answers the service's SUBSCRIBE and streams 30 s of a 124 bpm
drum-and-bass loop as ``room-mic`` (with heartbeats) plus a real MAESTRO .mid from config/live/test-piano/ as
``test-piano`` MIDI. A WS client on /v1/show checks what comes out: music.features at ~25 Hz with a valid spectrum32,
music.clock locking at 124 bpm, music.note in order on the audio timeline, music.notes and music.harmony, show.state
in K3 shape with ``global.music`` and no fixtures, /healthz and /api/v1/state. Art-Net out (the virtual Maestro) is
pointed at an ephemeral UDP socket standing in for room's receiver (never 6454, never room's API): frames at <= 44 Hz
with the DIMMER pulse edges on the predicted beats, and on the loop's real beats to a constant.

Runs about 35 s (the loop is real time). ``-s`` prints the measured numbers.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
import urllib.request

import aiohttp
import numpy as np
import pytest

from show_service import __main__ as main_mod
from show_service import artnet_out, config, contract
from show_service import clock as clockmod
from show_service.tests.synth_sender import BPM, MID_FILE, SynthSender, drum_and_bass_loop, midi_events

SECONDS = 30.0
PERIOD = 60.0 / BPM

pytestmark = pytest.mark.skipif(not MID_FILE.exists(), reason=f"{MID_FILE.name} not in config/live/test-piano/")


class ArtnetSink:
    """An ephemeral UDP socket standing in for room's Art-Net receiver: keeps (arrival_s, universe data, the clock's
    published body at arrival) per ArtDmx packet."""

    def __init__(self, snapshot_fn) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.snapshot_fn = snapshot_fn
        self.frames: list[tuple[float, bytes, dict | None]] = []
        self.bad = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="e2e-artnet-sink", daemon=True)
        self._t.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data = self.sock.recv(1024)
            except (socket.timeout, OSError):
                continue
            t = time.monotonic()
            if (data[:8] != artnet_out.ARTNET_ID or int.from_bytes(data[8:10], "little") != artnet_out.OP_DMX
                    or len(data) < artnet_out.HEADER + 2):
                self.bad += 1
                continue
            self.frames.append((t, data[artnet_out.HEADER:], self.snapshot_fn()))

    def stop(self) -> None:
        self._stop.set()
        self._t.join(2.0)
        self.sock.close()


async def listen(url: str, sender: SynthSender, deadline: float) -> list[tuple[float, dict]]:
    """Every bus message, with its arrival time, until the sender is done (or the deadline)."""
    out: list[tuple[float, dict]] = []
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url, max_msg_size=0)
        await ws.send_json({"type": "hello", "v": 2, "role": "e2e", "client_id": "test_e2e"})
        while time.monotonic() < deadline and not sender.done.is_set():
            try:
                msg = await ws.receive(timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            out.append((time.monotonic(), json.loads(msg.data)))
        await ws.close()
    return out


def get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, json.loads(r.read())


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOW_ROOM_DMX_URL", "")             # no yield poll: never room's API
    loop_audio = drum_and_bass_loop(SECONDS, BPM)
    events = midi_events(MID_FILE, SECONDS - 0.5)
    snd = SynthSender(loop_audio, events)
    snd.start()
    show = None
    sink = ArtnetSink(lambda: show.clock.snapshot() if show is not None and show.clock is not None else None)
    assert sink.port != artnet_out.PORT and snd.port != contract.BUS_PORT
    opts = config.Options(
        config=contract.Config(senders=(contract.Sender("e2e", "localhost", snd.port, ("room-mic", "test-piano")),),
                               vban_bind=("127.0.0.1", 0), bus_bind=("127.0.0.1", 0),
                               latency_file=tmp_path / "latency.json"),
        artnet_target=("127.0.0.1", sink.port))
    switch = sys.getswitchinterval()
    sys.setswitchinterval(0.001)                             # as main() does
    try:
        show = main_mod.Show(opts)
        show.start()
        assert show.bus.port not in (0, contract.BUS_PORT)
        base = f"http://127.0.0.1:{show.bus.port}"
        mid = {}

        async def run() -> list:
            async def probe() -> None:                      # mid-run: health, state, Art-Net status
                await asyncio.sleep(SECONDS * 0.6)
                loop = asyncio.get_running_loop()
                mid["healthz"] = await loop.run_in_executor(None, get, base + "/healthz")
                mid["state"] = await loop.run_in_executor(None, get, base + "/api/v1/state")
            task = asyncio.create_task(probe())
            msgs = await listen(show.bus.url, snd, time.monotonic() + SECONDS + 15)
            await task
            return msgs
        msgs = asyncio.run(run())
    finally:
        sys.setswitchinterval(switch)
        if show is not None:
            show.stop()
        snd.stop()
        sink.stop()

    t_start = snd.t_start
    assert t_start is not None and snd.subscribes >= 1, "no SUBSCRIBE reached the sender"
    assert snd.sent["errors"] == 0 and snd.sent["audio"] >= int(SECONDS * 48000 / 128) - 2
    by: dict[str, list[tuple[float, dict]]] = {}
    for t, m in msgs:
        by.setdefault(m["type"], []).append((t, m))
    assert msgs[0][1]["type"] == "welcome"
    report = []

    # -- music.features: ~25 Hz, spectrum32 of 32 in 0..1 ------------------------------------------------------------
    feats = [(t, m["data"]) for t, m in by.get(contract.MUSIC_FEATURES, [])]
    assert len(feats) > 20 * SECONDS * 0.8, len(feats)
    steady = [t for t, _ in feats if t_start + 2.0 <= t <= t_start + SECONDS - 1.0]
    rate = (len(steady) - 1) / (steady[-1] - steady[0])
    report.append(f"features {rate:.1f} Hz")
    assert 22.0 <= rate <= 27.0, rate
    for _, d in feats:
        s = d["spectrum32"]
        assert len(s) == 32 and all(0.0 <= v <= 1.0 for v in s), s
        assert d["stream"] == "room-mic" and set(d["bands"]) == set(contract.BAND_NAMES)
    late = [d for t, d in feats if t > t_start + 5.0]
    assert max(max(d["spectrum32"]) for d in late) > 0.3 and max(d["energy"] for d in late) > 0.05

    # -- music.clock: locks at 124 bpm ------------------------------------------------------------------------------
    clocks = [m["data"] for _, m in by.get(contract.MUSIC_CLOCK, [])]
    assert len(clocks) >= 10 * SECONDS * 0.8, len(clocks)
    assert set(clocks[-1]) == set(contract.ClockData.__annotations__)
    locked = [d for d in clocks if d["state"] == "locked" and abs(d["bpm"] - BPM) <= 2.0]
    assert locked, [(d["state"], round(d["bpm"], 1), d["conf"]) for d in clocks[::10]]
    t_lock = locked[0]["t_ms"] / 1000.0 - t_start
    tail = [d["bpm"] for d in clocks if d["t_ms"] / 1000.0 > t_start + SECONDS - 10.0 and d["state"] == "locked"]
    report.append(f"clock locked {t_lock:.1f} s after the first packet, bpm {np.median(tail):.2f} (last 10 s)")
    assert t_lock < 15.0 and len(tail) > 50 and abs(float(np.median(tail)) - BPM) < 1.0

    # -- music.note: source test, t_ms monotone, on the sender's timeline to a constant ----------------------------
    notes = [m["data"] for _, m in by.get(contract.MUSIC_NOTE, [])]
    file_on = [(t, d1) for t, st, d1, d2 in events if st & 0xF0 == 0x90 and d2 > 0]
    ons = [d for d in notes if d["on"]]
    assert len(ons) >= 0.9 * len(file_on), (len(ons), len(file_on))
    assert all(d["source"] == "test" and d["sess"] == notes[0]["sess"] for d in notes)
    tms = np.array([d["t_ms"] for d in notes])
    assert np.all(np.diff(tms) >= 0.0), np.diff(tms).min()
    assert np.all(np.diff([d["seq"] for d in notes]) >= 1)
    resid = []                                               # t_ms - the key's true time on the sender's clock
    for d, (t_file, pitch) in zip(ons, file_on):
        assert d["pitch"] == pitch
        resid.append(d["t_ms"] - (t_start + t_file) * 1000.0)
    resid = np.array(resid)
    report.append(f"notes {len(ons)} on / {len(notes)}; t_ms - true {np.median(resid):+.2f} ms "
                  f"(p5..p95 {np.percentile(resid, 5):+.2f}..{np.percentile(resid, 95):+.2f})")
    assert np.percentile(resid, 95) - np.percentile(resid, 5) < 5.0 and abs(np.median(resid)) < 30.0

    # -- music.notes and music.harmony -------------------------------------------------------------------------------
    nlist = [m["data"] for _, m in by.get(contract.MUSIC_NOTES, [])]
    assert len(nlist) >= 2 * SECONDS * 0.5 and any(d["present"] and d["keys"] for d in nlist)
    assert all(d["source"] == "test" for d in nlist)
    harm = [m["data"] for _, m in by.get(contract.MUSIC_HARMONY, [])]
    assert len(harm) >= 10 * SECONDS * 0.5
    assert all(len(d["chroma12"]) == 12 for d in harm) and any(d["source"] == "midi" for d in harm)
    keys = {(d["key"], d["mode"]) for d in harm if d["key"]}
    report.append(f"harmony {len(harm)} frames, keys {sorted(keys)}")
    assert keys and all(k in contract.KEY_NAMES for k, _ in keys)

    # -- show.state: K3 shape, global.music, no fixtures ------------------------------------------------------------
    states = [m for _, m in by.get("show.state", [])]
    assert len(states) >= 20 * SECONDS * 0.8
    st = states[len(states) // 2]
    assert "fixtures" not in st and st["feed"] == "live" and st["input"] == {"kind": "show", "online": True}
    music = st["global"]["music"]
    assert len(music["spectrum32"]) == 32 and len(music["chroma12"]) == 12 and music["stream"] == "room-mic"
    assert st["audio"]["active"] == "room-mic" and st["audio"]["streams"]["room-mic"]["state"] == "streaming"
    assert "activity" in st["global"]

    # -- /healthz and /api/v1/state, mid-run ------------------------------------------------------------------------
    code, h = mid["healthz"]
    assert code == 200 and h["ok"] is True and h["active_audio"] == "room-mic"
    assert h["streams"] == {"room-mic": "streaming", "test-piano": "streaming"}
    code, body = mid["state"]
    assert code == 200 and all(v == "ok" for v in body["modules"].values()) and len(body["modules"]) == 6
    assert body["errors"] == {} and body["piano"]["present"] is True
    assert body["artnet"]["state"] == "sending" and body["clock"]["stats"]["errors"] == 0
    assert 38.0 <= body["artnet"]["rate_hz"] <= 44.5                           # the send side's own rate
    assert body["streams"]["test-piano"]["clock"]["via"] == "audio"            # MIDI on the audio timeline
    report.append(f"clock source {body['clock']['stats']['source']}, dsp hop p95 "
                  f"{body['features_stats']['hop_ms_p95']} ms, clock cost p95 "
                  f"{body['clock']['stats']['cost_ms_per_10ms']['p95']} ms per 10 ms")

    # -- Art-Net out: <= 44 Hz, pulse edges on the predicted beats --------------------------------------------------
    frames = sink.frames
    assert sink.bad == 0 and len(frames) > 30 * SECONDS * 0.5, len(frames)
    lay = artnet_out.load_layout(artnet_out.PATCH_FILE)
    north = next(d for d in lay.devices if d.id == "screen.north")
    dim = next(c.index for c in north.channels if c.field == "pulse")
    ts = np.array([t for t, _, _ in frames])
    gaps = np.diff(ts)
    report.append(f"artnet {len(frames)} frames, {1.0 / np.median(gaps):.1f} Hz median, min gap "
                  f"{gaps.min() * 1000:.1f} ms")
    assert 38.0 <= 1.0 / np.median(gaps) <= 44.5 and np.percentile(gaps, 1) >= 0.015   # sent >= 22.7 ms apart;
    #                                                    the sink thread adds its own GIL jitter on arrival
    errs, true_errs = [], []
    prev = 0
    for t, data, body in frames:
        v = data[dim]
        if v >= 20 and v > prev + 5 and body is not None and body["gate"] == "beat":
            b = clockmod.beat_at(body, t)                    # the beat the clock predicted at that moment
            errs.append((b - round(b)) * 60.0 / body["bpm"] * 1000.0)
            k = (t - t_start) / PERIOD                       # the loop's own beat (sender time)
            true_errs.append((k - round(k)) * PERIOD * 1000.0)
        prev = v
    errs, true_errs = np.array(errs), np.array(true_errs)
    locked_s = SECONDS - t_lock
    report.append(f"pulse edges {len(errs)} (~{locked_s / PERIOD:.0f} beats locked); vs predicted beat "
                  f"median {np.median(errs):+.2f} ms p95 |err| {np.percentile(np.abs(errs), 95):.2f} ms; vs the "
                  f"loop's beats median {np.median(true_errs):+.2f} ms, spread p5..p95 "
                  f"{np.percentile(true_errs, 5):+.2f}..{np.percentile(true_errs, 95):+.2f} ms")
    assert len(errs) >= 0.7 * (locked_s - 2.0) / PERIOD, len(errs)
    assert abs(np.median(errs)) < 3.0 and np.percentile(np.abs(errs), 95) < 10.0
    assert np.percentile(true_errs, 95) - np.percentile(true_errs, 5) < 15.0 and abs(np.median(true_errs)) < 30.0
    print("\n[e2e] " + "\n[e2e] ".join(report))
