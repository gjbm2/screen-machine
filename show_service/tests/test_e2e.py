"""End to end: every real module wired by ``__main__.Show``, in-process, on ephemeral 127.0.0.1 ports only.

A synthetic VBAN sender (tests/synth_sender.py) answers the service's SUBSCRIBE and streams 30 s of a 124 bpm
drum-and-bass loop as ``room-mic`` (with heartbeats) plus a real MAESTRO .mid from config/live/test-piano/ as
``test-piano`` MIDI. A WS client on /v1/show checks what comes out: music.features at ~25 Hz with a valid spectrum32,
music.clock locking at 124 bpm, music.note in order on the audio timeline, music.notes and music.harmony, show.state
in K3 shape with ``global.music`` and no fixtures, /healthz and /api/v1/state. Art-Net out (the virtual Maestro) is
pointed at an ephemeral UDP socket standing in for room's receiver (never 6454, never room's API): frames at <= 44 Hz
with the DIMMER pulse edges on the predicted beats, and on the loop's real beats to a constant.

Runs about 35 s (the loop is real time). ``-s`` prints the measured numbers.

``test_brain_end_to_end`` (Stage 3, about 30 s) runs the same wiring with the brain on, against test_autopilot's
FakeRoom on an ephemeral port: the test-audio child plays a fetched track (copied into tmp; a synthetic loop when
none is fetched) -> features and clock -> the director's room.direction/look/event; PUT /api/v1/vibe changes the
look and Art-Net's colours; nudges through the API, a restart of the in-process service (state.json in tmp), the
test piano child and Auto's switch (source autopilot, expect, skip) and its hold after a person's switch. The listener
gets a fake LLM with no usable model, so nothing is ever uploaded.

``test_stage4_end_to_end`` (Stage 4, about 2.5 minutes; skipped without ffmpeg, the ML venv, the models or the test
tracks): three fetched tracks back to back through the test-audio child, with the real ML worker reading the real
models through a tmp directory of symlinks: CED, genre parents, music.context's sections, track changes, structure's
events on the bus, the director's reaction and the clock across a same-tempo and a tempo change (README S4.12).

``test_stage5_end_to_end`` (Stage 5, about 30 s; skipped without ffmpeg): the rig in the same wiring (README S5.12
step 1). Test music through the test-audio child -> the director's look -> the look engine -> artnet_lan's frames on
the beat grid to an ephemeral local sink and a virtual node, and hue_out's 50 Hz frames to a FAKE Hue bridge (a fake
CLIP client and DTLS streamer: never the real bridge). Lamp roles reach Hue, the screens are never scaled (Lights low),
and a hand pick on a screen holds the screens' palette until Back to auto. Every file in tmp_path; the real
fixtures.yaml, show.yaml and patch.yaml are read only, copied and edited in tmp.
"""

from __future__ import annotations

import asyncio
import gc
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

import aiohttp
import numpy as np
import pytest
import yaml

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
    state_dir = tmp_path / "state"                           # the mic on demand (README S5.16): room-mic captures only
    state_dir.mkdir()                                        # while something needs it, here the mode's claim
    (state_dir / "mic.json").write_text(json.dumps({"v": 1, "muted": False, "mode": True}))
    opts = config.Options(
        config=contract.Config(senders=(contract.Sender("e2e", "localhost", snd.port, ("room-mic", "test-piano")),),
                               vban_bind=("127.0.0.1", 0), bus_bind=("127.0.0.1", 0),
                               latency_file=tmp_path / "latency.json"),
        artnet_target=("127.0.0.1", sink.port), state_dir=state_dir)
    switch = sys.getswitchinterval()
    sys.setswitchinterval(0.001)                             # as main() does
    try:
        show = main_mod.Show(opts)
        show.start(freeze_gc=True)          # as main() does: after the whole suite the heap is big, and a full
        assert show.bus.port not in (0, contract.BUS_PORT)       # collection held the GIL long enough to slow Art-Net
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
        gc.unfreeze()                       # the rest of the suite collects as usual

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


# =====================================================================================================================
# Stage 3, end to end (README "Stage 3"): the brain in the real process wiring, against a fake room_service.
# =====================================================================================================================
from show_service.tests.test_autopilot import AUTO, PIANO_SKIP, FakeRoom  # noqa: E402

E2E_VIBES = """version: 1
order: [e2e-lively, e2e-calm]
vibes:
  e2e-lively:
    label: E2E lively
    energy: [0.3, 0.9]
    follow: [energy, onsets]
    beat_lock: true
    change_looks: time
    dwell_s: [4, 8]
    listener: false
    palettes: [ember, ocean, neon]
    chroma_max: 0.3
    saturation: [0.6, 1.0]
    level: [0.5, 1.0]
    floor: 0.2
    accents: beat
  e2e-calm:
    label: E2E calm
    palettes: [ice]
"""
STAGE3_MODES = {
    "gallery": {"label": "Normal"},
    "piano": {"label": "Piano", "autopilot": {"enter_when": {"piano_playing": {"for_s": 3}},
                                              "leave_when": {"piano_idle": {"for_s": 600}}, "skip": PIANO_SKIP}},
    "disco": {"label": "Disco"},
}


class RealClock:
    """FakeRoom's clock, on real time."""

    @property
    def t(self) -> float:
        return time.monotonic()


class RoomThread:
    """test_autopilot's FakeRoom on its own loop and thread (an ephemeral 127.0.0.1 port, never :8700)."""

    def __init__(self, modes: dict, auto: dict) -> None:
        self.loop = asyncio.new_event_loop()
        self.t = threading.Thread(target=self.loop.run_forever, name="e2e-fake-room", daemon=True)
        self.t.start()
        self.room = FakeRoom(RealClock(), modes, auto, on="on")
        self.room.since = time.monotonic() - 3600
        self.run(self.room.start())

    def run(self, coro, timeout: float = 5.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self) -> None:
        try:
            self.run(self.room.stop())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.t.join(2.0)


class BusTap:
    """A WS client on /v1/show (role e2e) in its own thread: every message with its arrival time. ``want``: the
    hello's want list (None: everything but the want-only types)."""

    def __init__(self, url: str, want: list | None = None) -> None:
        self.url, self.msgs, self.want = url, [], want
        self._stop = threading.Event()
        self.t = threading.Thread(target=lambda: asyncio.run(self._run()), name="e2e-bus-tap", daemon=True)
        self.t.start()

    async def _run(self) -> None:
        async with aiohttp.ClientSession() as s:
            ws = await s.ws_connect(self.url, max_msg_size=0)
            hello = {"type": "hello", "v": 2, "role": "e2e", "client_id": "test_e2e_brain"}
            if self.want is not None:
                hello["want"] = self.want
            await ws.send_json(hello)
            while not self._stop.is_set():
                try:
                    msg = await ws.receive(timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                self.msgs.append((time.monotonic(), json.loads(msg.data)))
            await ws.close()

    def of(self, type_: str) -> list[dict]:
        return [m["data"] for _, m in list(self.msgs) if m.get("type") == type_]

    def stop(self) -> None:
        self._stop.set()
        self.t.join(3.0)


class FakeLLM:
    """utils.llm stand-in: no model is usable, so nothing is ever uploaded (and OpenAI is never called)."""

    def status(self) -> dict:
        return {"tasks": {"listen": {"usable": [], "models": []}, "describe": {"usable": [], "models": []}}}

    def over_budget(self, task: str) -> bool:
        return False

    async def acall_json(self, *a, **kw):
        raise AssertionError("the e2e test never calls an LLM")


def http(method: str, url: str, body: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_for(pred, timeout: float, what: str, every: float = 0.1):
    end = time.monotonic() + timeout
    while True:
        got = pred()
        if got:
            return got
        assert time.monotonic() < end, f"timed out waiting for {what}"
        time.sleep(every)


def udp_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
        if p not in (6454, 6980, 6981, 6982, 8700, 8701, 8702, 8703):
            return p


def pick_music(dest: pathlib.Path) -> tuple[str, str]:
    """(piece id, how): one fetched track copied into ``dest`` (a steady-beat genre first), else a synthetic WAV of
    the drum-and-bass loop. The real test-music folder is only read."""
    dest.mkdir(parents=True, exist_ok=True)
    src = contract.TEST_MUSIC_DIR
    try:
        index = json.loads((src / "index.json").read_text())
    except (OSError, ValueError):
        index = {}
    for genre in ("techno", "house", "pop", "rock"):
        for tid, e in index.items():
            f = src / str(e.get("file", ""))
            if e.get("genre") == genre and f.is_file():
                shutil.copy(f, dest / f.name)
                (dest / "index.json").write_text(json.dumps({tid: e}))
                return f"file:{f.name}", f"fetched {genre}: {e.get('title')} ({e.get('artist')})"
    pcm = drum_and_bass_loop(40.0, BPM)
    with wave.open(str(dest / "loop.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(pcm.reshape(-1), -1, 1) * 32767).astype("<i2").tobytes())
    return "file:loop.wav", f"synthetic {BPM:g} bpm loop"


def first_piece(base: str) -> str:
    code, t = http("GET", base + "/api/v1/test")
    assert code == 200, t
    files = [p["id"] for p in t["pieces"]["piano"] if p["id"].startswith("file:")]
    jazz = [p for p in files if "Jazz" in p]
    return (jazz or files)[0]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg decodes the test audio")
def test_brain_end_to_end(tmp_path, monkeypatch):
    """Test audio (a child process) -> features and clock -> the director publishes room.direction/look/event; a
    vibe through PUT /api/v1/vibe changes the look and Art-Net's colours; nudges through the API change it and survive
    a restart of the in-process service; the test piano (POST /api/v1/test) plays notes; Auto switches the fake room
    to the mode whose enter_when piano_playing holds (source autopilot, expect, skip) and holds after a person's
    switch. Ephemeral ports, a fake room, a fake LLM, every file in tmp_path."""
    monkeypatch.setenv("SHOW_ROOM_DMX_URL", "")                 # no yield poll: never room's API
    report: list[str] = []
    room = RoomThread(STAGE3_MODES, AUTO)
    music_dir = tmp_path / "music"
    piece, how = pick_music(music_dir)
    report.append(f"test audio: {how}")
    p_piano, p_audio = udp_port(), udp_port()
    real = yaml.safe_load(contract.SHOW_FILE.read_text())      # c8's lamps/director sections, read only
    settings = {"version": 1,
                "senders": {"test-piano": {"hosts": ["localhost"], "port": p_piano, "streams": ["test-piano"],
                                           "on_demand": True},
                            "test-audio": {"hosts": ["localhost"], "port": p_audio, "streams": ["test-audio"],
                                           "on_demand": True, "room_sound": False}},
                "tests": {"piano": {"sender": "test-piano", "pieces": str(contract.TEST_PIANO_DIR)},
                          "audio": {"sender": "test-audio", "music": str(music_dir), "rank_first": True}},
                "room": {"url": room.room.url},
                "situations": {"midi": {"streams": ["test-piano"], "include_test": True, "channels": None,
                                         "heard_by": None, "window_s": 6, "min_notes": 6, "min_span_s": 2,
                                         "min_pitches": 3},
                               "music": {"stream": "test-audio", "dsp_min_gate": 0.3},
                               "quiet": {"stream": "test-audio", "gate_below": 0.05}},
                "lamps": real.get("lamps"), "director": real.get("director"),
                "listener": {"music_gate": "dsp"},
                "ml": {"enabled": False}}                    # Stage 4's worker: test_stage4_end_to_end runs it
    show_file = tmp_path / "show.yaml"
    show_file.write_text(yaml.safe_dump(settings, sort_keys=False))
    vibes_file = tmp_path / "vibes.yaml"
    vibes_file.write_text(E2E_VIBES)
    show = None
    sink = ArtnetSink(lambda: None)
    opts = config.Options(config=contract.Config(vban_bind=("127.0.0.1", 0), bus_bind=("127.0.0.1", 0),
                                                 latency_file=tmp_path / "latency.json"),
                          artnet_target=("127.0.0.1", sink.port), brain=True, show_file=show_file,
                          vibes_file=vibes_file, state_dir=tmp_path / "state")
    brain_kw = {"listener": {"llm": FakeLLM(), "warm": False}}
    lay = artnet_out.load_layout(artnet_out.PATCH_FILE)
    north = next(d for d in lay.devices if d.id == "screen.north")
    rgb_idx = [next(c.index for c in north.channels if c.field == f) for f in ("r", "g", "b")]
    amber = tuple(round(c * 255) for c in artnet_out.DEFAULT_RGB)
    tap = None
    switch = sys.getswitchinterval()
    sys.setswitchinterval(0.001)
    try:
        # -- A: test audio -> features/clock -> the director; a vibe; nudges ------------------------------------------
        show = main_mod.Show(opts, brain_kw=brain_kw)
        show.start()
        base = f"http://127.0.0.1:{show.bus.port}"
        tap = BusTap(show.bus.url)
        assert all(v == "ok" for v in show.modules.values()), show.modules
        code, st = http("POST", base + "/api/v1/test", {"source": "audio", "piece": piece})
        assert code == 200 and st["state"] == "starting" and st["stream"] == "test-audio", st
        wait_for(lambda: http("GET", base + "/api/v1/test")[1]["sources"]["audio"]["state"] == "playing", 15,
                 "test audio playing")
        wait_for(lambda: show.health().get("active_audio") == "test-audio", 10, "test-audio active (rank_first)")
        t_active = time.monotonic()
        assert show.rx.health()["priority"][:1] == ["test-audio"]
        # neutral first: published with vibe null, Art-Net keeps the fixed colour
        wait_for(lambda: any(d["vibe"] is None for d in tap.of(contract.ROOM_LOOK)), 6, "a neutral room.look")
        assert show.director.palette_fn(time.monotonic()) is None
        wait_for(lambda: len([f for f in tap.of(contract.MUSIC_FEATURES) if f["stream"] == "test-audio"]) > 100, 10,
                 "features from test-audio")
        time.sleep(max(0.0, t_active + 8.0 - time.monotonic()))     # let the clock find the beat
        # degrade, never refuse: a feel vibes.yaml doesn't have is kept as asked and plays neutral, and says so
        code, odd = http("PUT", base + "/api/v1/vibe", {"vibe": "no-such-feel"})
        assert code == 200 and odd["vibe"] == "no-such-feel" and odd["known"] is False and odd["problems"], odd
        assert show.director.palette_fn(time.monotonic()) is None
        code, rb = http("PUT", base + "/api/v1/vibe", {"vibe": "e2e-lively", "listening": False})
        assert code == 200 and rb["vibe"] == "e2e-lively" and rb["known"] is True and rb["source"] == "mode", rb
        # the mode's "listening" no longer gates (README S5.20): the listener's own switch says why (off by default)
        assert rb["listening_effective"] is False and rb["listening_why"] == "AI listening is off"
        assert rb["problems"] == [] and rb["boot_id"] == odd["boot_id"]
        look = wait_for(lambda: next((d for d in reversed(tap.of(contract.ROOM_LOOK)) if d["vibe"] == "e2e-lively"),
                                     None), 5, "room.look for the vibe")
        assert look["palette"]["name"] in ("ember", "ocean", "neon"), look["palette"]
        assert show.director.palette_fn(time.monotonic()) is not None
        n_frames = len(sink.frames)
        wait_for(lambda: len(sink.frames) > n_frames + 40, 5, "Art-Net frames")
        colours = {tuple(data[i] for i in rgb_idx) for _, data, _ in sink.frames[-20:]}
        assert colours and amber not in colours, (colours, amber)  # the director's colour, not the fixed amber
        report.append(f"vibe set: look {look['look_id']} palette {look['palette']['name']}; north screen RGB "
                      f"{sorted(colours)[0]} (fixed colour was {amber})")
        # nudges
        code, out = http("POST", base + "/api/v1/step", {"dir": "wilder"})
        assert code == 200 and out["steps"]["value"] == 1 and any(c["id"] == "steps" for c in out["chips"]), out
        n_ev = len(tap.of(contract.ROOM_EVENT))
        code, out = http("PUT", base + "/api/v1/palette", {"name": "ocean"})
        assert code == 200 and out["palette"]["pick"] == "ocean", out
        ev = wait_for(lambda: next((e for e in tap.of(contract.ROOM_EVENT)[n_ev:] if e["kind"] == "look_change"),
                                   None), 3, "a look_change event for the palette pick")
        wait_for(lambda: any(d["vibe"] == "e2e-lively" and d["palette"]["name"] == "ocean"
                             for d in tap.of(contract.ROOM_LOOK)), 6, "room.look with the picked palette")
        code, out = http("POST", base + "/api/v1/action", {"do": "next"})
        assert code == 200 and out["done"] is True and out["look"]["next_pending"] is True, out
        code, out = http("POST", base + "/api/v1/action", {"do": "half"})            # a clock word
        assert code == 200 and out["done"] is True
        code, out = http("POST", base + "/api/v1/action", {"do": "double"})
        assert code == 200 and out["done"] is True
        wait_for(lambda: len(tap.of(contract.ROOM_EVENT)) >= n_ev + 2, 6, "the Next look event")
        directions = tap.of(contract.ROOM_DIRECTION)
        assert directions and directions[-1]["vibe"] == "e2e-lively"
        card = http("GET", base + "/api/v1/card")[1]
        assert card["vibe"]["name"] == "e2e-lively" and card["steps"]["value"] == 1 and card["palette"]["pick"] == "ocean"
        assert card["hearing"]["stream"] == "test-audio" and card["hearing"]["test"] is True
        assert card["tests"]["audio"]["state"] == "playing"
        clocks = [d for d in tap.of(contract.MUSIC_CLOCK) if d["t_ms"] / 1000.0 > t_active]
        locked = [d for d in clocks if d["state"] == "locked"]
        feats = [f for f in tap.of(contract.MUSIC_FEATURES) if f["stream"] == "test-audio"]
        events = tap.of(contract.ROOM_EVENT)
        report.append(f"features {len(feats)} from test-audio; clock {len(clocks)} bodies, locked "
                      f"{len(locked)}{' at %.1f bpm' % locked[-1]['bpm'] if locked else ''}; room.direction "
                      f"{len(directions)}, room.look {len(tap.of(contract.ROOM_LOOK))}, room.event {len(events)} "
                      f"({', '.join(sorted({e['detail'] for e in events}))})")
        assert clocks and ev["look_id"].startswith("L")
        st = http("GET", base + "/api/v1/state")[1]
        assert st["brain"]["running"] and st["errors"] == {}, st["errors"]
        assert all(t["errors"] == 0 for t in st["brain"]["ticks"].values()), st["brain"]["ticks"]
        report.append("brain ticks p95 ms: " + ", ".join(f"{n} {t['p95_ms']}" for n, t in st["brain"]["ticks"].items())
                      + f"; loop lag p95 {st['brain']['loop_lag_ms_p95']} ms")
        time.sleep(1.3)                                             # state.json within PERSIST_MIN_S
        tap.stop()
        show.stop()                                                 # stops the test-audio child too
        saved = json.loads((tmp_path / "state" / "state.json").read_text())
        assert saved["director"]["target"] == {"vibe": "e2e-lively", "listening": False}
        assert saved["director"]["nudges"]["steps"] == 1 and saved["director"]["nudges"]["palette"] == "ocean"

        # -- B: a restart of the in-process service keeps the vibe and the nudges --------------------------------------
        show = main_mod.Show(opts, brain_kw=brain_kw)
        show.start()
        base = f"http://127.0.0.1:{show.bus.port}"
        tap = BusTap(show.bus.url)
        rb = http("GET", base + "/api/v1/vibe")[1]
        assert rb["vibe"] == "e2e-lively" and rb["source"] == "restored", rb
        wait_for(lambda: http("GET", base + "/api/v1/card")[1]["palette"]["pick"] == "ocean", 3, "restored nudges")
        card = http("GET", base + "/api/v1/card")[1]
        assert card["steps"]["value"] == 1 and {c["id"] for c in card["chips"]} >= {"steps", "palette"}
        assert card["tests"]["audio"]["state"] == "off"                 # tests never survive a restart

        # -- C: the test piano, then Auto ----------------------------------------------------------------------------
        assert room.room.auto_puts() == []
        pc = first_piece(base)
        code, st = http("POST", base + "/api/v1/test", {"source": "piano", "piece": pc})
        assert code == 200 and st["state"] == "starting", st
        wait_for(lambda: http("GET", base + "/api/v1/test")[1]["sources"]["piano"]["state"] == "playing", 15,
                 "test piano playing")
        wait_for(lambda: len([n for n in tap.of(contract.MUSIC_NOTE) if n["on"]]) >= 5, 10, "music.note from the piano")
        notes = tap.of(contract.MUSIC_NOTE)
        assert all(n["source"] == "test" for n in notes)
        put = wait_for(lambda: next(iter(room.room.auto_puts()), None), 25, "Auto's PUT /api/v1/mode")
        assert put["mode"] == "piano" and put["source"] == contract.AUTOPILOT_SOURCE and put["_current"] == "gallery"
        assert put["skip"] == PIANO_SKIP and put["expect"] == {"current": "gallery", "last_transition_id": None}
        assert "piano is playing" in put["reason"], put["reason"]
        wait_for(lambda: room.room.current == "piano", 5, "the fake room in piano")
        ap = wait_for(lambda: (lambda a: a if a.get("state") == "watching" and (a.get("entered") or {}).get("mode")
                               == "piano" else None)(http("GET", base + "/api/v1/autopilot")[1]), 5, "Auto watching")
        report.append(f"test piano {pc!r}: {len([n for n in notes if n['on']])} note-ons; Auto PUT {put['mode']} "
                      f"({put['reason']}); then {ap['line']!r}")
        wait_for(lambda: http("GET", base + "/api/v1/card")[1]["steps"]["value"] == 0, 3, "nudges cleared by the mode")
        # a person picks Disco: Auto holds, and asks for nothing while the piano still plays
        room.run(room.room.person("disco"))
        ap = wait_for(lambda: (lambda a: a if a.get("state") == "holding" else None)(
            http("GET", base + "/api/v1/autopilot")[1]), 5, "Auto holding")
        n_puts = len(room.room.auto_puts())
        time.sleep(4.0)
        assert len(room.room.auto_puts()) == n_puts == 1 and room.room.current == "disco"
        assert http("GET", base + "/api/v1/test")[1]["sources"]["piano"]["state"] == "playing"
        report.append(f"after a person's switch: {ap['state']} ({ap['line']}), no further PUT in 4 s")
        log_ = http("GET", base + "/api/v1/log?kind=autopilot&n=20")[1]["entries"]
        assert any(e.get("kind") == "switch" for e in log_), log_
        assert http("DELETE", base + "/api/v1/test")[1]["stopped"] == ["piano"]
    finally:
        sys.setswitchinterval(switch)
        if tap is not None:
            tap.stop()
        if show is not None:
            show.stop()
        sink.stop()
        room.stop()
    assert all(p["source"] != contract.AUTOPILOT_SOURCE or p["mode"] != p["_current"] for p in room.room.puts)
    print("\n[e2e brain] " + "\n[e2e brain] ".join(report))


# =====================================================================================================================
# Stage 4, end to end (README "Stage 4"): the ML worker (its own venv, the real models through a tmp symlinked
# directory), dsp's medium lane, the clock's show-beat thread, structure and the consumers, on real music.
# =====================================================================================================================
#: Three fetched tracks back to back, each EXCERPT_S from OFFSET_S, with GAP_S of silence after each (structure's
#: track rule needs track.gap_s = 3 s of silence). Tempos measured with show_service's own clock on these excerpts
#: (16 Oct): house 120.0, jazz 145.8, pop 135.1. The house is time-stretched (ffmpeg atempo, pitch kept) to the
#: jazz's tempo, so the first change keeps the tempo (the clock stays locked through it) and the second changes it by
#: 7 % (the clock re-locks). The genre parents differ across both changes (Electronic, then Jazz/Rock, then Pop or
#: Electronic). (id, bpm as fetched, atempo factor)
STAGE4_TRACKS = (("house-galeria-disco-memorias", 120.0, 145.8 / 120.0),
                 ("jazz-doxent-1973", 145.8, 1.0),
                 ("pop-lilly-wolf-play-the-game", 135.1, 1.0))
STAGE4_OFFSET_S, STAGE4_EXCERPT_S, STAGE4_GAP_S = 40.0, 45.0, 4.0
#: Discogs parents a listener would accept for each track (the model's own labels; a test, not config)
PLAUSIBLE_PARENTS = {"house": {"Electronic"}, "techno": {"Electronic"},
                     "jazz": {"Jazz", "Funk / Soul", "Blues", "Latin", "Rock"},   # "1973" reads as jazz-rock
                     "pop": {"Pop", "Electronic", "Rock", "Funk / Soul", "Hip Hop"},
                     "rock": {"Rock", "Pop"}, "classical": {"Classical", "Stage & Screen"}}
STAGE4_VIBES = E2E_VIBES + """  e2e-phrase:
    label: E2E phrase
    beat_lock: true
    change_looks: phrase
    change_rate: 0.5
    listener: false
    palettes: [ember, ocean, neon]
"""


def _decode(path: pathlib.Path, offset: float, seconds: float, tempo: float) -> np.ndarray:
    af = ["-af", f"atempo={tempo:.6f}"] if abs(tempo - 1.0) > 1e-6 else []
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(offset), "-t", f"{seconds * tempo:.3f}",
                          "-i", str(path), *af, "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32).copy()[: int(seconds * 48000)]
    ramp = np.linspace(0.0, 1.0, 2400, dtype=np.float32)          # 50 ms fades: no clicks at the cuts
    x[:2400] *= ramp
    x[-2400:] *= ramp[::-1]
    return x


def stage4_music(dest: pathlib.Path) -> tuple[str, list[dict]]:
    """(piece id, the schedule): the three excerpts and their gaps as one WAV in ``dest`` (the fetched files are only
    read). Each schedule entry: id, genre, title, start_s and end_s within the file, bpm (after the stretch)."""
    dest.mkdir(parents=True, exist_ok=True)
    index = json.loads((contract.TEST_MUSIC_DIR / "index.json").read_text())
    parts, sched, pos = [], [], 0.0
    gap = np.zeros(int(STAGE4_GAP_S * 48000), np.float32)
    for tid, bpm, tempo in STAGE4_TRACKS:
        e = index[tid]
        x = _decode(contract.TEST_MUSIC_DIR / e["file"], STAGE4_OFFSET_S, STAGE4_EXCERPT_S, tempo)
        sched.append({"id": tid, "genre": e["genre"], "title": e["title"], "start_s": pos,
                      "end_s": pos + x.size / 48000.0, "bpm": bpm * tempo})
        parts += [x, gap]
        pos += (x.size + gap.size) / 48000.0
    pcm = np.concatenate(parts)
    with wave.open(str(dest / "stage4.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    (dest / "index.json").write_text(json.dumps({"stage4": {"file": "stage4.wav", "genre": "mixed",
                                                            "title": " / ".join(e["title"] for e in sched),
                                                            "artist": "test-music excerpts"}}))
    return "file:stage4.wav", sched


def _stage4_ready() -> str | None:
    if shutil.which("ffmpeg") is None:
        return "ffmpeg decodes the test audio"
    if not contract.ML_PYTHON.exists():
        return f"the ML venv ({contract.ML_PYTHON}) isn't built"
    if not contract.MODELS_INDEX.exists():
        return "the models aren't fetched (tools/show/fetch_models.py)"
    try:
        index = json.loads((contract.TEST_MUSIC_DIR / "index.json").read_text())
    except (OSError, ValueError):
        return "no test music (tools/show/fetch_test_music.py)"
    missing = [t for t, _, _ in STAGE4_TRACKS if t not in index or not (contract.TEST_MUSIC_DIR /
                                                                         index[t]["file"]).is_file()]
    return f"test tracks missing: {missing}" if missing else None


@pytest.mark.skipif(_stage4_ready() is not None, reason=str(_stage4_ready()))
def test_stage4_end_to_end(tmp_path, monkeypatch):
    """Three tracks of different genres back to back through the real test-audio child, with the real ML worker
    (~/.venvs/sm-show-ml; the real models through a tmp directory of symlinks, never written): CED hears music, the
    genre parents are plausible for each track, music.context carries sections, a track change is detected between
    tracks, structure's room.events reach the bus, the director reacts with structure's looks, and the clock stays
    locked through the same-tempo change and re-locks at the new tempo. Fake room, ephemeral ports, every file in
    tmp_path, a fake LLM. About 2.5 minutes (the music is real time); ``-s`` prints what it heard."""
    monkeypatch.setenv("SHOW_ROOM_DMX_URL", "")
    report: list[str] = []
    room = RoomThread(STAGE3_MODES, {**AUTO, "on": False})
    piece, sched = stage4_music(tmp_path / "music")
    models = tmp_path / "models"
    models.mkdir()
    for f in contract.MODELS_DIR.iterdir():                       # read through symlinks; the real dir is never written
        (models / f.name).symlink_to(f)
    p_audio = udp_port()
    real = yaml.safe_load(contract.SHOW_FILE.read_text())          # c8's sections, read only
    ml = json.loads(json.dumps(real["ml"]))
    ml.update(python=str(contract.ML_PYTHON), models_dir=str(models))
    settings = {"version": 1,
                "senders": {"test-audio": {"hosts": ["localhost"], "port": p_audio, "streams": ["test-audio"],
                                           "on_demand": True, "room_sound": False}},
                "tests": {"audio": {"sender": "test-audio", "music": str(tmp_path / "music"), "rank_first": True}},
                "room": {"url": room.room.url},
                "situations": {**real["situations"], "music": {**real["situations"]["music"], "stream": "test-audio"},
                               "quiet": {**real["situations"]["quiet"], "stream": "test-audio"},
                               "talking": {**real["situations"]["talking"], "stream": "test-audio"},
                               "midi": {**real["situations"]["midi"], "heard_by": None}},
                "lamps": real["lamps"], "director": real["director"], "listener": real["listener"], "ml": ml,
                "genre_map": real["genre_map"], "structure": real["structure"]}
    show_file = tmp_path / "show.yaml"
    show_file.write_text(yaml.safe_dump(settings, sort_keys=False))
    vibes_file = tmp_path / "vibes.yaml"
    vibes_file.write_text(STAGE4_VIBES)
    sink = ArtnetSink(lambda: None)
    opts = config.Options(config=contract.Config(vban_bind=("127.0.0.1", 0), bus_bind=("127.0.0.1", 0),
                                                 latency_file=tmp_path / "latency.json"),
                          artnet_target=("127.0.0.1", sink.port), brain=True, show_file=show_file,
                          vibes_file=vibes_file, state_dir=tmp_path / "state")
    show = tap = rtap = None
    switch = sys.getswitchinterval()
    sys.setswitchinterval(0.001)
    ced_rows: dict[float, dict] = {}
    tag_rows: dict[float, dict] = {}
    try:
        show = main_mod.Show(opts, brain_kw={"listener": {"llm": FakeLLM(), "warm": False}})
        show.start()
        base = f"http://127.0.0.1:{show.bus.port}"
        tap = BusTap(show.bus.url)
        rtap = BusTap(show.bus.url, want=[contract.MUSIC_RHYTHM])
        assert all(v == "ok" for v in show.modules.values()), show.modules
        assert show.ml is not None and show.mlfeed is not None
        wait_for(lambda: show.ml.view()["state"] == "running", 40, "the ML worker's hello")
        hello = show.ml.view()["hello"]
        assert hello["tasks"] == {"ced": True, "tags": True, "key": True, "beats": True}, hello
        assert hello["nice"] == 19 and hello["threads"]["compute"] <= 2
        report.append("worker: " + ", ".join(f"{k} {v}" for k, v in sorted(hello["models"].items())))
        code, rb = http("PUT", base + "/api/v1/vibe", {"vibe": "e2e-phrase", "listening": False})
        assert code == 200 and rb["known"] is True, rb
        code, st = http("POST", base + "/api/v1/test", {"source": "audio", "piece": piece})
        assert code == 200 and st["stream"] == "test-audio", st

        def first_sound():
            return next((f["t_ms"] / 1000.0 for f in tap.of(contract.MUSIC_FEATURES)
                         if f["stream"] == "test-audio" and f["rms_dbfs"] > -50.0), None)
        t0 = wait_for(first_sound, 20, "the first test-audio sound")    # the file's start, within a publish
        end = t0 + sched[-1]["end_s"] + 1.0
        while time.monotonic() < end:                              # the ML view, read as another thread would
            v = show.ml.view()
            for c in v["ced_recent"]:
                ced_rows[c["t"]] = c
            if v["tags"] is not None:
                tag_rows[v["tags"]["t"]] = v["tags"]
            time.sleep(0.5)
        st = http("GET", base + "/api/v1/state")[1]
        mlsnap, events = st["ml"], tap.of(contract.ROOM_EVENT)
        contexts, clocks = tap.of(contract.MUSIC_CONTEXT), tap.of(contract.MUSIC_CLOCK)
        structure = st["brain"]["components"]["structure"]
        director = st["brain"]["components"]["director"]
        cst = (st.get("clock") or {}).get("stats") or {}
        clock_counts = {k: cst.get(k, 0) for k in ("relocks", "cold_searches", "cold_alt", "octave_changes",
                                                    "corr_accepted", "corr_applied", "corr_stale", "corr_expired")}
        clock_counts["last_correction"] = cst.get("last_correction")
        clock_counts["beat_thread"] = cst.get("beat_thread")
        report.append(f"Beat This! votes: applied {mlsnap['applied']}, confirmed {mlsnap['confirmed']}; last "
                      + ", ".join(f"{v['octave']}/{v['bar_len']}/{v['beat_is_one']} conf {v['conf']} {v['why']}"
                                  for v in mlsnap["votes"][-4:]))
        report.append(f"show-rx per block: on_audio {st['rx_ms']['on_audio']}, on_tick {st['rx_ms']['on_tick']}; "
                      f"clock block {cst.get('block_ms')}")
    finally:
        sys.setswitchinterval(switch)
        for t in (tap, rtap):
            if t is not None:
                t.stop()
        if show is not None:
            show.stop()
        sink.stop()
        room.stop()

    (tmp_path / "stage4.json").write_text(json.dumps(          # what it heard, for a look afterwards
        {"t0": t0, "sched": sched, "clocks": clocks, "contexts": contexts, "events": events,
         "ced": list(ced_rows.values()), "tags": [{k: v for k, v in g.items() if k != "emb"} for g in tag_rows.values()],
         "ml": mlsnap, "structure": structure, "director_sections": director.get("sections")}, default=str))
    try:
        _stage4_checks(report, sched, settings, t0, ced_rows, tag_rows, mlsnap, events, contexts, clocks, structure,
                       director, clock_counts, rtap, tap)
    finally:
        print("\n[e2e stage 4] " + "\n[e2e stage 4] ".join(report))


def _stage4_checks(report, sched, settings, t0, ced_rows, tag_rows, mlsnap, events, contexts, clocks, structure,
                   director, clock_counts, rtap, tap) -> None:
    """Every check is made and reported before the first failure is raised (a 2.5-minute run says everything)."""
    fails: list[str] = []

    def check(ok: object, what: object) -> None:
        if not ok:
            fails.append(str(what))

    def within(t: float, e: dict, lo: float = 3.0, hi: float = 1.0) -> bool:
        return t0 + e["start_s"] + lo <= t <= t0 + e["end_s"] - hi

    # -- what the worker heard, per track -------------------------------------------------------------------------
    leaders = []
    for e in sched:
        ced = [c["music_p"] for t, c in ced_rows.items() if within(t, e)]
        tags = [g for t, g in tag_rows.items() if within(t, e)]
        check(len(ced) >= 20 and len(tags) >= 20, (e["id"], len(ced), len(tags)))
        if not ced or not tags:
            continue
        parents: dict[str, float] = {}
        for g in tags:
            for k, val in g["genre_parents"].items():
                parents[k] = parents.get(k, 0.0) + val / len(tags)
        top = sorted(parents.items(), key=lambda kv: -kv[1])[:3]
        styles = {}
        for g in tags:
            for lab, val in g["styles_top3"]:
                styles[lab] = styles.get(lab, 0.0) + val / len(tags)
        leaders.append((e, top, float(np.median(ced))))
        report.append(f"{e['genre']} ({e['title']}, {e['bpm']:.1f} bpm): CED music median {np.median(ced):.2f} "
                      f"(n {len(ced)}); parents " + ", ".join(f"{k} {val:.2f}" for k, val in top) + "; styles "
                      + ", ".join(f"{k} {val:.2f}" for k, val in sorted(styles.items(), key=lambda kv: -kv[1])[:3]))
    tasks = mlsnap["stats"]["tasks"]
    report.append("worker tasks: " + "; ".join(f"{k} runs {t['runs']} skipped {t['skipped']} over {t['over_budget']} "
                                               f"p95 {t['cost_ms_p95']} ms" for k, t in tasks.items())
                  + f"; cpu {mlsnap['cpu']}; feed drops {mlsnap['feed']['drops']}; bad lines {mlsnap['bad_lines']}")
    for e, top, ced_med in leaders:
        check(ced_med >= 0.5, ("CED music", e["id"], ced_med))
        check(top[0][0] in PLAUSIBLE_PARENTS[e["genre"]], ("genre parent", e["genre"], top))
    check(mlsnap["bad_lines"] == 0 and mlsnap["restarts"] == 0, ("worker", mlsnap["bad_lines"], mlsnap["restarts"]))
    check(tasks["ced"]["runs"] >= 100 and tasks["tags"]["runs"] >= 100 and tasks["beats"]["runs"] >= 5, tasks)

    # -- music.context: sections and the track changes ------------------------------------------------------------
    check(contexts and all(set(c) == set(contract.MusicContext.__annotations__) for c in contexts), "context shape")
    in_music = [c for c in contexts if any(within(c["t_ms"] / 1000.0, e) for e in sched)]
    check(sum(c["source"] == "ml" for c in in_music) >= 0.8 * len(in_music), "sections on the ML embeddings")
    sec_ids = sorted({c["section"]["id"] for c in contexts})
    letters = sorted({c["section"]["letter"] for c in contexts if c["section"]["letter"]})
    check(len(sec_ids) >= 3, ("sections", sec_ids))
    kinds: dict[str, int] = {}
    for ev in events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    changes = [ev for ev in events if ev["kind"] == "track_change"]
    report.append(f"music.context {len(contexts)} (source ml {sum(c['source'] == 'ml' for c in contexts)}); "
                  f"sections {sec_ids[:12]}{'...' if len(sec_ids) > 12 else ''} letters {letters}; track changes "
                  + ", ".join(f"{ev['at_ms'] / 1000.0 - t0:.1f} s via {ev['via']}" for ev in changes)
                  + "; real changes at " + ", ".join("%.0f s" % e["start_s"] for e in sched[1:]) + "; room.event "
                  + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    check(changes, "no track change detected")
    win = settings["structure"]["track"]["window_s"]
    for ev in changes:                                             # each one in a real change's window
        at = ev["at_ms"] / 1000.0 - t0
        check(any(e["start_s"] - STAGE4_GAP_S - 1.0 <= at <= e["start_s"] + win + 6.0 for e in sched[1:]),
              ("a track change away from a real one", at))
    check(contexts and max(c["track"]["id"] for c in contexts) >= len(changes), "music.context track ids")
    for kind in ("section_change", "track_change"):                # structure's events on the bus
        check(kinds.get(kind, 0) >= 1, ("room.event", kind, kinds))
    rhythm = rtap.of(contract.MUSIC_RHYTHM)
    check(len(rhythm) > 500 and not tap.of(contract.MUSIC_RHYTHM), ("music.rhythm want-only", len(rhythm)))
    # GOBO: with a vibe playing the director owns it (a step per look that lands); structure steps it only neutral
    check(structure["gobo"]["steps"] + director["stats"]["gobo_steps"] >= 1,
          ("GOBO", structure["gobo"], director["stats"]["gobo_steps"]))

    # -- the director reacts to structure ---------------------------------------------------------------------------
    looks = [ev for ev in events if ev["kind"] == "look_change" and ev.get("detail") in ("section", "repeat", "drop",
                                                                                          "track")]
    # §8.5: a confirmed boundary is heard ~5 s late (more than max_late_bars), so it shows inside the look (kept and
    # expressed); a new song on a sure grid makes a "track" look. Either is the director reacting.
    sstats = director["sections"]["stats"]
    report.append(f"director: structure looks {[ev['detail'] for ev in looks]}; section stats looks "
                  f"{sstats['looks']} kept {sstats['kept']} expressed {sstats['expressed']}; events seen "
                  f"{sstats['events']}")
    check(looks or sstats["expressed"] >= 1, "the director didn't react to structure (no look, nothing expressed)")
    check(director["sections"]["structure"] is True and sum(sstats["events"].values()) >= 2, sstats["events"])

    # -- the clock: locked through the same-tempo change, re-locked at the new tempo ------------------------------
    def clock_in(a: float, b: float) -> list[dict]:
        return [c for c in clocks if a <= c["t_ms"] / 1000.0 - t0 < b]

    def locked_at(e: dict, within_s: float) -> float | None:
        for c in clock_in(e["start_s"], e["start_s"] + within_s):
            if c["state"] == "locked" and abs(c["bpm"] / e["bpm"] - 1.0) <= 0.02:
                rest = clock_in(c["t_ms"] / 1000.0 - t0, e["end_s"] - 1.0)
                good = [r for r in rest if r["state"] in ("locked", "holding")
                        and abs(r["bpm"] / e["bpm"] - 1.0) <= 0.02]
                if len(good) >= 0.8 * len(rest):
                    return c["t_ms"] / 1000.0 - t0 - e["start_s"]
        return None
    lock = [locked_at(e, 15.0) for e in sched]
    a, b = sched[0], sched[1]
    across = clock_in(a["end_s"] - 5.0, b["start_s"] + (lock[1] or 15.0))
    dropped = [c for c in across if c["bpm"] == 0.0 or c["state"] in ("searching", "free")]
    medians = [round(float(np.median([c["bpm"] for c in clock_in(e["start_s"] + 15, e["end_s"])] or [0.0])), 1)
               for e in sched]
    for e in sched:
        cs = clock_in(e["start_s"], e["end_s"])
        states = {k: sum(c["state"] == k for c in cs) for k in ("locked", "holding", "searching", "free")}
        gates = {k: sum(c["gate"] == k for c in cs) for k in ("beat", "dmx", "none")}
        report.append(f"clock on {e['genre']}: {len(cs)} bodies, states {states}, gates {gates}, conf median "
                      f"{float(np.median([c['conf'] for c in cs] or [0.0])):.2f}")
    report.append("clock: locked " + ", ".join(f"{e['genre']} {lk:.1f} s after its start" if lk is not None else
                                                f"{e['genre']} never" for e, lk in zip(sched, lock))
                  + f"; across the same-tempo change {len(dropped)} of {len(across)} bodies without a grid; "
                  f"medians {medians} bpm; counts {clock_counts}")
    check(all(lk is not None for lk in lock), ("clock lock", lock))
    check(not dropped and (lock[1] or 99.0) <= 8.0, ("the grid through the same-tempo change", lock, dropped[:3]))
    assert not fails, fails


# =====================================================================================================================
# Stage 5, end to end (README "Stage 5", S5.12 step 1): the rig in the real process wiring. Test music through the
# real test-audio child -> features and clock -> the director's look -> the look engine -> artnet_lan's frames on the
# beat grid to an ephemeral local sink (and a virtual node's kept universe), and hue_out's 50 Hz frames to a FAKE Hue
# bridge (a fake CLIP client and a fake DTLS streamer: never the real bridge, never an Entertainment stream). The
# screens are never scaled; a hand pick on a screen holds the screens' palette until Back to auto.
# =====================================================================================================================
from show_service import hue_out as hue_mod  # noqa: E402

E2E_PAR = {"name": "E2E RGB par", "availableChannels": {
    "Dimmer": {"capability": {"type": "Intensity"}},
    "Red": {"capability": {"type": "ColorIntensity", "color": "Red"}},
    "Green": {"capability": {"type": "ColorIntensity", "color": "Green"}},
    "Blue": {"capability": {"type": "ColorIntensity", "color": "Blue"}}},
    "modes": [{"name": "4ch", "channels": ["Dimmer", "Red", "Green", "Blue"]}]}
HUE_TEST_HOST = "198.51.100.7"        # TEST-NET-2: only ever handed to the fake streamer


class LiveRoom(FakeRoom):
    """test_autopilot's FakeRoom with both screens live, emitting room's new ``screen.look`` event on every look PATCH
    (2f's room code: ``source`` echoed, default "web")."""

    def __init__(self, *a, screens=(), **kw) -> None:
        super().__init__(*a, **kw)
        self.live_screens = tuple(screens)

    def status(self) -> dict:
        st = super().status()
        st["screens"] = {s: {"observed": {"layer": "live", "phase": "live"}} for s in self.live_screens}
        return st

    async def h_look(self, r):
        resp = await super().h_look(r)
        sid, b = self.look_patches[-1]
        await self.send({"type": contract.ROOM_SCREEN_LOOK, "data": {
            "screen": sid, "session": "e2e", "source": b.get("source", "web"),
            "changed": sorted(k for k in b if k != "source"), **({"palette": b["palette"]} if "palette" in b else {})}})
        return resp


class LiveRoomThread(RoomThread):
    """RoomThread with a LiveRoom (both screens live)."""

    def __init__(self, modes: dict, auto: dict, screens: list[str]) -> None:
        self.loop = asyncio.new_event_loop()
        self.t = threading.Thread(target=self.loop.run_forever, name="e2e-fake-room", daemon=True)
        self.t.start()
        self.room = LiveRoom(RealClock(), modes, auto, on="off", screens=screens)
        self.room.since = time.monotonic() - 3600
        self.run(self.room.start())


class NodeSink(ArtnetSink):
    """An Art-Net node standing in on an ephemeral port: ArtDmx kept as ArtnetSink keeps them, ArtPolls counted."""

    def __init__(self, snapshot_fn) -> None:
        self.polls = 0
        super().__init__(snapshot_fn)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data = self.sock.recv(1024)
            except (socket.timeout, OSError):
                continue
            t = time.monotonic()
            if data[:8] == artnet_out.ARTNET_ID and int.from_bytes(data[8:10], "little") == 0x2000:
                self.polls += 1                                   # ArtPoll (artnet_lan's discovery)
                continue
            if (data[:8] != artnet_out.ARTNET_ID or int.from_bytes(data[8:10], "little") != artnet_out.OP_DMX
                    or len(data) < artnet_out.HEADER + 2):
                self.bad += 1
                continue
            self.frames.append((t, data[artnet_out.HEADER:], self.snapshot_fn()))


class FakeHueBridge:
    """CLIP v2, aiohttp-free (test_hue_out's FakeClient, on the registry's own light ids): every call recorded.
    ``inventory`` is room's own (HueClient.inventory over these GETs), so room's resolve_area runs unchanged."""

    from room_service.hue.client import HueClient as _RoomHueClient
    inventory = _RoomHueClient.inventory

    def __init__(self, area_id: str, lights: list[str]) -> None:
        self.calls: list[tuple] = []
        self.area = {"id": area_id, "status": "inactive", "active_streamer": None,
                     "channels": [{"channel_id": i, "members": [{"service": {"rid": f"ent-{li}"}}]}
                                  for i, li in enumerate(lights)]}
        self.lights = [{"id": li, "owner": {"rid": f"dev-{li}"}} for li in lights]
        self.ents = [{"id": f"ent-{li}", "owner": {"rid": f"dev-{li}"}} for li in lights]

    async def get(self, rtype, rid=None):
        await asyncio.sleep(0)
        self.calls.append(("GET", rtype, rid))
        if rtype == "light":
            return [dict(x) for x in self.lights]
        if rtype == "entertainment":
            return [dict(x) for x in self.ents]
        if rtype == "entertainment_configuration":
            return [dict(self.area)] if rid in (None, self.area["id"]) else []
        return []

    async def put(self, rtype, rid, body):
        await asyncio.sleep(0)
        self.calls.append(("PUT", rtype, rid, body.get("action")))
        if body.get("action") == "start":
            self.area.update(status="active", active_streamer={"rid": "auth-e2e", "rtype": "auth_v1"})
        elif body.get("action") == "stop":
            self.area.update(status="inactive", active_streamer=None)
        return {"data": []}

    def puts(self, action: str) -> list:
        return [x for x in self.calls if x[0] == "PUT" and x[3] == action]


class FakeHueStreamer:
    """The DTLS streamer's surface: records each send_colors (monotonic s, commands, the director's palette then)."""

    def __init__(self) -> None:
        self.is_connected = False
        self.connects: list[tuple] = []
        self.sent: list[tuple[float, list, object]] = []
        self.probe = None                                    # () -> the director's palette now (set by the test)

    def connect(self, host, app_key, clientkey, area_id):
        assert host == HUE_TEST_HOST
        self.connects.append((host, area_id))
        self.is_connected = True

    def disconnect(self):
        self.is_connected = False

    def send_colors(self, cmds):
        t = time.monotonic()
        p = self.probe
        self.sent.append((t, list(cmds), p(t) if p is not None else None))


def _xy_of(rgb) -> tuple[int, int]:
    """hue_out's xy for an rgb (the full-level colour; black -> the white point), 16-bit."""
    lvl = max(rgb)
    n = tuple(c / lvl for c in rgb) if lvl > 0 else (0.0, 0.0, 0.0)
    x, y = hue_mod.rgb_to_xy(*n)
    return round(x * 0xFFFF), round(y * 0xFFFF)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg decodes the test audio")
def test_stage5_end_to_end(tmp_path, monkeypatch):
    """README S5.12 step 1. Ephemeral ports, a fake room (live screens, the screen.look event), a FAKE Hue bridge, a
    local Art-Net sink and a virtual node; every file in tmp_path; the real fixtures.yaml, show.yaml and patch.yaml
    read only (copied and edited in tmp)."""
    # -- guards: never the real bridge, never the real pairing files --------------------------------------------------
    def refuse(*_a, **_k):
        raise AssertionError("a default Hue factory was called: the e2e uses fakes only")
    monkeypatch.setattr(hue_mod, "default_client_factory", refuse)
    monkeypatch.setattr(hue_mod, "default_streamer_factory", refuse)
    from room_service.hue import client as room_client, pair as room_pair
    for mod in (room_client, room_pair):                       # room's BridgeSecrets.load (hue_out's) reads tmp only
        monkeypatch.setattr(mod, "SECRETS_DIR", tmp_path / "hue")
    monkeypatch.setattr(contract, "HUE_SECRETS_DIR", tmp_path / "hue")
    monkeypatch.setattr(hue_mod, "CONTROL_WAKE_S", 0.1)
    monkeypatch.setenv("SHOW_ROOM_DMX_URL", "")                 # no yield poll: never room's API
    report: list[str] = []

    # -- the room's files, copied into tmp and edited -------------------------------------------------------------------
    patch = yaml.safe_load(contract.PATCH_FILE.read_text())
    patch["hue"]["streamer"] = "show"                           # 68's switch, flipped in the copy only
    patch["hue"]["host"] = HUE_TEST_HOST                        # room's rule: patch.yaml's host over the file's
    patch_file = tmp_path / "patch.yaml"
    patch_file.write_text(yaml.safe_dump(patch, sort_keys=False))
    monkeypatch.setenv("SHOW_PATCH_FILE", str(patch_file))      # artnet_out, the director and the rig all read it
    secrets = tmp_path / "hue"
    secrets.mkdir()
    (secrets / f"{patch['hue']['bridge']}.json").write_text(json.dumps({
        "host": HUE_TEST_HOST, "bridge_id": patch["hue"]["bridge"], "name": "fake", "model": "fake",
        "app_key": "e2e-app-key", "clientkey": "00112233445566778899aabbccddeeff"}))
    show = None
    dmx_sink = NodeSink(lambda: show.clock.snapshot() if show is not None and show.clock is not None else None)
    vm_sink = ArtnetSink(lambda: None)                          # the virtual Maestro's (room's receiver stand-in)
    assert artnet_out.PORT not in (dmx_sink.port, vm_sink.port)
    fx = yaml.safe_load(contract.FIXTURES_FILE.read_text())
    fx["outputs"]["hue"]["enabled"] = True
    fx["outputs"]["dmx-a"] = {"driver": "artnet", "enabled": True, "node": "sink", "port_address": 1}
    fx["outputs"]["dmx-v"] = {"driver": "artnet", "enabled": True, "node": "previz", "port_address": 2}
    fx["nodes"] = {"sink": {"last_ip": "127.0.0.1", "port": dmx_sink.port}, "previz": {"virtual": True}}
    fx["types"]["par"] = {"source": "ofl", "file": "ofl/e2e/par.json", "mode": "4ch"}
    fx["fixtures"]["par.a"] = {"type": "par", "output": "dmx-a", "address": 1, "screen": "north-screen",
                               "zone": "north", "placement": "above"}
    fx["fixtures"]["par.b"] = {"type": "par", "output": "dmx-a", "address": 5, "screen": "south-screen",
                               "zone": "south", "placement": "opposite"}
    fx["fixtures"]["par.v"] = {"type": "par", "output": "dmx-v", "address": 1, "screen": "north-screen",
                               "placement": "above"}
    fx_file = tmp_path / "fixtures.yaml"
    fx_file.write_text(yaml.safe_dump(fx, sort_keys=False))
    types_dir = tmp_path / "fixture-types"
    (types_dir / "ofl" / "e2e").mkdir(parents=True)
    (types_dir / "ofl" / "e2e" / "par.json").write_text(json.dumps(E2E_PAR))
    lamps = sorted(f for f, d in fx["fixtures"].items() if d.get("output") == "hue")
    light_of = {f: fx["fixtures"][f]["light"] for f in lamps}
    screens = sorted(f for f, d in fx["fixtures"].items() if d.get("type") == "sm-screen")
    bridge = FakeHueBridge(patch["hue"]["area"], [light_of[f] for f in lamps])
    streamer = FakeHueStreamer()

    # -- the fake room, show.yaml, vibes --------------------------------------------------------------------------------
    room = LiveRoomThread(STAGE3_MODES, AUTO, sorted({fx["fixtures"][s]["screen"] for s in screens}))
    music_dir = tmp_path / "music"
    piece, how = pick_music(music_dir)
    report.append(f"test audio: {how}")
    p_audio = udp_port()
    real = yaml.safe_load(contract.SHOW_FILE.read_text())       # c8's lamps, director, rig and looks: read only
    settings = {"version": 1,
                "senders": {"test-audio": {"hosts": ["localhost"], "port": p_audio, "streams": ["test-audio"],
                                           "on_demand": True, "room_sound": False}},
                "tests": {"audio": {"sender": "test-audio", "music": str(music_dir), "rank_first": True}},
                "room": {"url": room.room.url},
                "situations": {"music": {"stream": "test-audio", "dsp_min_gate": 0.3},
                               "quiet": {"stream": "test-audio", "gate_below": 0.05}},
                "lamps": real.get("lamps"), "director": real.get("director"), "rig": real.get("rig"),
                "looks": real.get("looks"), "listener": {"music_gate": "dsp"}, "ml": {"enabled": False}}
    show_file = tmp_path / "show.yaml"
    show_file.write_text(yaml.safe_dump(settings, sort_keys=False))
    vibes_file = tmp_path / "vibes.yaml"
    vibes_file.write_text(E2E_VIBES)
    opts = config.Options(config=contract.Config(vban_bind=("127.0.0.1", 0), bus_bind=("127.0.0.1", 0),
                                                 latency_file=tmp_path / "latency.json"),
                          artnet_target=("127.0.0.1", vm_sink.port), brain=True, show_file=show_file,
                          vibes_file=vibes_file, state_dir=tmp_path / "state")
    brain_kw = {"listener": {"llm": FakeLLM(), "warm": False},
                "outputs": {"fixtures_path": fx_file, "types_dir": types_dir,
                            "driver_kw": {"hue": {"client_factory": lambda s: bridge,
                                                  "streamer_factory": lambda: streamer}}}}
    lay = artnet_out.load_layout(patch_file)
    vm_dev = {d.id: d for d in lay.devices}
    tap = None
    switch = sys.getswitchinterval()
    sys.setswitchinterval(0.001)
    try:
        show = main_mod.Show(opts, brain_kw=brain_kw)
        show.start()
        base = f"http://127.0.0.1:{show.bus.port}"
        assert show.bus.port not in (0, contract.BUS_PORT)
        tap = BusTap(show.bus.url)
        assert all(v == "ok" for v in show.modules.values()), show.modules
        streamer.probe = show.director.palette_fn
        # white box: what artnet_lan's dmx-a driver targeted for each frame (the plan, the beat it carries, when it
        # left) and the clock's body then
        lan = next(d for d in show.rig.drivers_of("artnet") if d.output_id == "dmx-a")
        lan_rec: list[tuple] = []

        def rec_set_frame(frame, _orig=lan.set_frame, _d=lan):
            t0, up, planned, body = time.monotonic(), _d._upcoming, _d._planned, show.clock.snapshot()
            _orig(frame)
            lan_rec.append((t0, planned, up, _d._sent_at, body))
        lan.set_frame = rec_set_frame
        snap = http("GET", base + "/api/v1/rig")[1]
        assert snap["registry"]["problems"] == [] and snap["registry"]["counts"]["fixtures"] == 9, snap["registry"]
        assert snap["outputs"]["screens"]["state"] == "off" and set(snap["outputs"]) == {"screens", "hue", "dmx-a",
                                                                                        "dmx-v"}
        assert sorted(t for t in show.state()["threads"] if t.startswith("show-out-")) == [
            "show-out-dmx-a", "show-out-dmx-v", "show-out-hue"]
        # room's hue adapter asks for the lamps (and a screen, which is never a lamp of the stream)
        code, hs = http("POST", base + "/api/v1/hue/stream", {"lights": lamps + [screens[0]], "on": True})
        assert code == 200 and hs["wanted"] == lamps and hs["unknown"] == [screens[0]], hs
        assert hs["state"] in ("armed", "streaming") and set(hs) == set(contract.HueAnswer.__annotations__), hs
        code, st = http("POST", base + "/api/v1/test", {"source": "audio", "piece": piece})
        assert code == 200 and st["stream"] == "test-audio", st
        wait_for(lambda: http("GET", base + "/api/v1/test")[1]["sources"]["audio"]["state"] == "playing", 15,
                 "test audio playing")
        wait_for(lambda: show.health().get("active_audio") == "test-audio", 10, "test-audio active")
        t_active = time.monotonic()
        hs = wait_for(lambda: (lambda s: s if s["state"] == "streaming" else None)(
            http("GET", base + "/api/v1/hue/status")[1]), 15, "hue streaming")
        assert hs["words"] == "Following the music", hs
        hs = http("GET", base + "/api/v1/rig")[1]["hue"]               # the driver's own view
        assert hs["dtls"] is True and sorted(hs["channels"]) == sorted(light_of.values()), hs
        assert bridge.puts("start") and streamer.connects == [(HUE_TEST_HOST, patch["hue"]["area"])]
        time.sleep(max(0.0, t_active + 8.0 - time.monotonic()))     # let the clock find the beat
        code, rb = http("PUT", base + "/api/v1/vibe", {"vibe": "e2e-lively", "listening": False})
        assert code == 200 and rb["known"] is True, rb
        look = wait_for(lambda: next((d for d in reversed(tap.of(contract.ROOM_LOOK)) if d["vibe"] == "e2e-lively"),
                                     None), 5, "room.look for the vibe")
        time.sleep(3.0)                                             # the entry fade settles (crossfades still fine)

        # -- a measured window: Hue, DMX, the virtual node ------------------------------------------------------------
        t_a = time.monotonic()
        time.sleep(6.0)
        t_b = time.monotonic()
        look = show.director.look_fn()
        roles = {f: look["lamps"][f]["role"] for f in lamps + ["par.a", "par.b", "par.v"]}
        place_roles = real["lamps"]["placement_roles"]
        assert roles == {f: place_roles[fx["fixtures"][f]["placement"]] for f in roles}, roles
        assert all(max(s["rgb"]) == pytest.approx(1.0, abs=1e-3) for s in look["screens"].values()), look["screens"]
        # Hue: 50 Hz, every lamp every frame, each lamp in its own colour from the director (its role's)
        sends = [s for s in streamer.sent if t_a <= s[0] <= t_b]
        ts = np.array([s[0] for s in sends])
        rate = (len(ts) - 1) / (ts[-1] - ts[0])
        gaps = np.diff(ts)
        report.append(f"hue {len(sends)} frames in {t_b - t_a:.1f} s: {rate:.2f} Hz, gap p50 "
                      f"{np.median(gaps) * 1000:.1f} ms p99 {np.percentile(gaps, 99) * 1000:.1f} ms")
        assert 48.0 <= rate <= 51.0 and np.percentile(gaps, 99) < 0.045, (rate, np.percentile(gaps, 99))
        chan = {i: f for i, f in enumerate(lamps)}                 # FakeHueBridge: channel i = lamps[i]
        xy_err, bri = [], {f: [] for f in lamps}
        for t, cmds, pal_t in sends:
            assert sorted(c[0] for c in cmds) == list(range(len(lamps))), cmds
            for ch, x, y, b in cmds:
                f = chan[ch]
                bri[f].append(b)
                if isinstance(pal_t, dict) and f in pal_t:
                    ex, ey = _xy_of(pal_t[f])
                    xy_err.append(max(abs(x - ex), abs(y - ey)) / 0xFFFF)
        report.append(f"hue xy vs the director's colour: p50 {np.median(xy_err):.4f} p95 "
                      f"{np.percentile(xy_err, 95):.4f} (xy units); bri range " + ", ".join(
                          f"{f} {min(v)}..{max(v)}" for f, v in bri.items()))
        assert len(xy_err) > 0.9 * len(sends) * len(lamps) and np.percentile(xy_err, 95) < 0.01
        key = [f for f in lamps if roles[f] != roles[lamps[0]]]
        last = {chan[ch]: (x, y) for ch, x, y, _ in sends[-1][1]}
        assert len({last[f] for f in lamps}) >= 2 and key, last  # the roles differ on the lamps (extend vs key)
        # parity of colour with the virtual Maestro's hue channels (its frames from the same palette_fn)
        vm = [(t, d) for t, d, _ in vm_sink.frames if t_a <= t <= t_b]
        par = []
        for t, d in vm[::5]:
            j = int(np.searchsorted(ts, t))
            if 0 < j < len(ts) and isinstance(sends[j][2], dict):
                for f in lamps:
                    rgbi = [next(ch.index for ch in vm_dev[f].channels if ch.field == k) for k in ("r", "g", "b")]
                    rgb = [d[i] / 255.0 for i in rgbi]
                    if max(rgb) > 0.1:
                        hx = next(c for c in sends[j][1] if chan[c[0]] == f)
                        par.append(max(abs(hx[1] - _xy_of(rgb)[0]), abs(hx[2] - _xy_of(rgb)[1])) / 0xFFFF)
        report.append(f"hue vs the virtual Maestro's hue channels (colour): {len(par)} pairs, p50 "
                      f"{np.median(par):.4f} p95 {np.percentile(par, 95):.4f}")
        assert par and np.median(par) < 0.01
        # DMX: frames to the sink on the beat grid (a frame leaves at t_beat - L, L unset = 0, and on every 16th)
        frames = [(t, d, b) for t, d, b in dmx_sink.frames if t_a <= t <= t_b]
        assert dmx_sink.bad == 0 and dmx_sink.polls >= 1 and len(frames) > 10, (dmx_sink.bad, len(frames))
        # the driver's beat frames: planned exactly on the beat it carries (t_k - L, L unset = 0), left on time, and
        # that beat is the clock's
        recs = [r for r in lan_rec if t_a <= r[0] <= t_b]
        beat_frames = [r for r in recs if r[2] is not None and r[1] is not None and r[1] >= r[2] - 1e-9
                       and r[3] is not None and r[3] >= r[0]]
        late = np.array([(r[3] - r[2]) * 1000.0 for r in beat_frames])
        assert beat_frames, "no beat frames"
        vs_clock = []
        for _, _, up, _, body in beat_frames:
            if body and body.get("bpm"):
                b = clockmod.beat_at(body, up)
                vs_clock.append((b - round(b)) * 60.0 / body["bpm"] * 1000.0)
        vs_clock = np.array(vs_clock)
        bpm = next((b["bpm"] for _, _, b in reversed(frames) if b and b.get("bpm")), 0.0)
        n_beats = (t_b - t_a) * bpm / 60.0
        report.append(f"dmx beat frames {len(beat_frames)} of ~{n_beats:.0f} beats ({bpm:.1f} bpm): left "
                      f"{np.median(late):+.2f} ms after the beat (p95 {np.percentile(late, 95):.2f}); the beat vs the "
                      f"clock's at that moment |err| p50 {np.median(np.abs(vs_clock)):.2f} p90 "
                      f"{np.percentile(np.abs(vs_clock), 90):.2f} ms")
        assert len(beat_frames) >= 0.8 * n_beats and np.median(late) < 2.0 and np.percentile(late, 95) < 5.0, (
            len(beat_frames), late)                                 # niced, under the test's own load
        assert np.median(np.abs(vs_clock)) < 1.0 and np.percentile(np.abs(vs_clock), 90) < 5.0, vs_clock
        # the 16th grid, at the sink (arrival time against the clock's body then: re-anchors add their own error)
        on_grid, n_grid = 0, 0
        for t, d, body in frames:
            if not body or body.get("gate") not in ("beat", "dmx") or not body.get("bpm"):
                continue
            n_grid += 1
            beat_s = 60.0 / body["bpm"]
            e = (clockmod.beat_at(body, t) % 1.0) * beat_s
            tick = artnet_out.grid_tick(beat_s)
            on_grid += abs((e / tick - round(e / tick)) * tick) < 0.003
        dbg = {k: v for k, v in http("GET", base + "/api/v1/rig")[1]["outputs"]["dmx-a"]["extra"].items()
               if k not in ("node", "caps", "problems")}
        iv = np.diff([t for t, _, _ in frames]) * 1000.0
        report.append(f"dmx {len(frames)} frames at the sink ({len(frames) / (t_b - t_a):.1f} Hz: a tick is skipped "
                      f"only when the last frame left over the grid's slack + 1 ms late, README S5.14, or a hit "
                      f"pulled one); on the 16th grid {on_grid}/"
                      f"{n_grid}; intervals p50 {np.median(iv):.1f} p95 {np.percentile(iv, 95):.1f} ms; driver period "
                      f"{dbg.get('period_ms')} ms, too_soon {dbg.get('too_soon')}")
        assert n_grid > 0.8 * len(frames) and on_grid >= 0.6 * n_grid, (on_grid, n_grid, dbg)
        dim = [d[0] for _, d, _ in frames]                         # par.a's dimmer: the look engine's intensity
        assert max(dim) - min(dim) > 20 and all(len(d) == 8 for _, d, _ in frames), (min(dim), max(dim))
        rs = http("GET", base + "/api/v1/rig")[1]
        runner = rs["outputs"]["dmx-a"]["extra"]["runner"]
        report.append(f"dmx-a: state {rs['outputs']['dmx-a']['state']}, runner hits {runner['hits']}, late p50 "
                      f"{runner['late_ms']['p50']} p95 {runner['late_ms']['p95']} ms; engine eval p95 "
                      + ", ".join(f"{o} {v['eval_us']['p95']} µs" for o, v in rs["engine"]["per_output"].items()))
        assert rs["outputs"]["hue"]["state"] == "streaming" and rs["outputs"]["dmx-a"]["state"] == "sending"
        assert runner["hits"] > 0 and rs["errors"] == {} and rs["engine"]["errors"] == {}, (runner, rs["errors"])
        # the virtual node: kept, decoded, never sent
        v = http("GET", base + "/api/v1/rig/dmx")[1]["nodes"]["previz"]["2"]
        fv = http("GET", base + "/api/v1/rig/frame?output=dmx-v")[1]
        assert v["length"] == 4 and fv["universe"]["virtual"] is True and fv["universe"]["to"] is None
        assert fv["fixtures"]["par.v"]["dmx"] is not None and set(fv["fixtures"]) == {"par.v"}
        # the screens rule: no screen in any output's frame
        for oid in ("hue", "dmx-a", "dmx-v"):
            got = set(http("GET", base + f"/api/v1/rig/frame?output={oid}")[1]["fixtures"])
            assert got and not got & set(screens), (oid, got)

        # -- screens are never scaled: Lights low dims lamps and DMX, never the screens -----------------------------
        code, out = http("POST", base + "/api/v1/action", {"do": "low"})
        assert code == 200 and out["done"] is True
        low_level = float(real["director"]["low"]["level"])
        wait_for(lambda: all(max(show.director.palette_fn(time.monotonic())[f]) <= low_level + 1e-3 for f in lamps),
                 10, "the director's lamps at Lights low")
        t_l = time.monotonic() + 0.1
        time.sleep(1.2)
        low_bri = [b for t, cmds, _ in streamer.sent if t >= t_l for _, _, _, b in cmds]
        scr = vm_dev[screens[0]]
        scr_rgb = [next(ch.index for ch in scr.channels if ch.field == k) for k in ("r", "g", "b")]
        vm_low = [max(d[i] for i in scr_rgb) for t, d, _ in vm_sink.frames if t >= t_l]
        dmx_low = [d[0] for t, d, _ in dmx_sink.frames if t >= t_l]
        report.append(f"lights low: hue bri max {max(low_bri)} (full {0xFFFF}), DMX dimmer p50 "
                      f"{np.median(dmx_low):.0f} max {max(dmx_low)} (no hits under Lights low), the virtual "
                      f"Maestro's {screens[0]} RGB max {min(vm_low)}..{max(vm_low)}")
        assert max(low_bri) <= (low_level + 0.005) ** float(patch["hue"]["gamma"]) * 0xFFFF, max(low_bri)
        assert max(dmx_low) <= 255 * (low_level + 0.01) and vm_low and min(vm_low) >= 254
        http("POST", base + "/api/v1/action", {"do": "low"})

        # -- the hand-pick hold: a person's pick on a screen holds the screens' palette until Back to auto ----------
        wait_for(lambda: any(b.get("source") == contract.SHOW_SOURCE for _, b in room.room.look_patches), 10,
                 "the director's screens palette PATCH, with source")
        sp = http("GET", base + "/api/v1/state")[1]["brain"]["components"]["director"]["screens_palette"]
        assert sp["look_source"] == "yes" and sp["hold"] is None, sp
        pick = next(p for p in ("ice", "ocean", "ember") if p != look["palette"]["name"])
        room.run(room.room.send({"type": contract.ROOM_SCREEN_LOOK, "data": {
            "screen": room.room.live_screens[0], "session": "phone", "source": "web", "changed": ["palette"],
            "palette": pick}}))
        chip = wait_for(lambda: next((c for c in http("GET", base + "/api/v1/card")[1]["chips"]
                                      if c["id"] == contract.SCREEN_PICK), None), 5, "the screen_pick chip")
        assert chip["value"]["palette"] == pick and "picked on a screen" in chip["label"], chip
        n_patch = len(room.room.look_patches)
        http("POST", base + "/api/v1/action", {"do": "next"})        # a new look: the screens still hold
        time.sleep(4.0)
        assert len(room.room.look_patches) == n_patch, room.room.look_patches[n_patch:]
        sp = http("GET", base + "/api/v1/state")[1]["brain"]["components"]["director"]["screens_palette"]
        assert sp["hold"]["palette"] == pick and sp["why"].startswith("held"), sp
        code, out = http("DELETE", base + "/api/v1/nudge/all")     # Back to auto
        assert code == 200, out
        wait_for(lambda: len(room.room.look_patches) > n_patch, 5, "the director's palette back on the screens")
        assert not [c for c in http("GET", base + "/api/v1/card")[1]["chips"] if c["id"] == contract.SCREEN_PICK]
        report.append(f"hold: pick {pick!r} on {room.room.live_screens[0]} held {len(room.room.look_patches) - n_patch}"
                      f" PATCHes back after Back to auto; the lamps carried on ({len(streamer.sent)} hue frames)")

        # -- release: room's adapter takes the lamps back --------------------------------------------------------------
        code, rel = http("POST", base + "/api/v1/hue/stream", {"lights": [], "on": False})   # room's order
        assert code == 200 and rel["state"] == "idle", rel
        code, rel = http("POST", base + "/api/v1/hue/release", {"lights": lamps})
        assert code == 200 and rel["released"] is True and rel["via"] == "show" and rel["state"] == "idle", rel
        assert bridge.puts("stop") and bridge.area["status"] == "inactive"
        st = http("GET", base + "/api/v1/state")[1]
        assert st["errors"] == {} and all(t["errors"] == 0 for t in st["brain"]["ticks"].values()), st["errors"]
        report.append("rig tick p95 " + str(st["brain"]["ticks"]["rig"]["cpu_p95_ms"]) + " ms CPU; director "
                      + str(st["brain"]["ticks"]["director"]["cpu_p95_ms"]) + " ms")
    finally:
        sys.setswitchinterval(switch)
        if tap is not None:
            tap.stop()
        if show is not None:
            show.stop()
        dmx_sink.stop()
        vm_sink.stop()
        room.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith("show-out-") and t.is_alive()]
    assert not (tmp_path / "never-the-real-secrets").exists()
    print("\n[e2e stage5] " + "\n[e2e stage5] ".join(report))
