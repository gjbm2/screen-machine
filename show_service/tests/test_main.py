"""show_service/__main__.py at the "does it crash" level: the wiring with fake modules (README interfaces), the
degraded start with every module missing, and ``python -m show_service`` up, answering /healthz, and out on SIGTERM.
Every bind is 127.0.0.1 on an ephemeral port; no senders, so nothing is sent anywhere."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request

import numpy as np

from show_service import __main__ as main_mod
from show_service import config, contract, vban
from show_service import midi as real_midi

HEALTH = {"active_audio": "room-mic", "streams": {"room-mic": {"kind": "audio", "state": "streaming"}}}
OPTS = config.Options(config=contract.Config(bus_bind=("127.0.0.1", 0), vban_bind=("127.0.0.1", 0)))


def _get(url: str, timeout: float = 2.0) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def install_fakes(monkeypatch) -> None:
    dsp = types.ModuleType("show_service.dsp")

    class Features:
        def __init__(self, *, publish=None, hop=256, publish_hz=25.0):
            self.publish, self.blocks = publish, 0

        def process(self, stream, n0, t0_host_s, pcm, fs, flags):
            if stream == "boom":
                raise RuntimeError("bad block")
            self.blocks += 1

        def tick(self, now_s):
            self.publish({"type": contract.MUSIC_FEATURES,
                          "data": {"t_ms": now_s * 1000.0, "stream": "room-mic", "energy": 0.5,
                                   "spectrum32": [0.1] * 32, "bands": {"sub": {"level": 0.2}}}})

        def chroma12(self):
            return None

        def latest(self):
            return None

        def stats(self):
            return {"blocks": self.blocks}

    dsp.Features = Features

    midi = types.ModuleType("show_service.midi")

    class Piano:
        def __init__(self, stream, publish, *, source=None, ignore_channels=(), state_channels=None):
            self.stream, self.publish, self.state_channels = stream, publish, state_channels
            self.present, self.sess, self.seq = False, f"{stream}-1", 0
            self.pedals = {"damper": 0.0, "soft": 0.0, "sostenuto": 0.0}
            self.msgs = []

        def on_midi(self, msg, arrival_s, to_host):
            self.msgs.append((msg, to_host(1)))
            self.present = True

        def tick(self, now_s):
            if self.stream == "broken-piano":
                raise ValueError("tick")

        def stats(self):
            return {"batches": len(self.msgs)}

    midi.Piano = Piano

    harmony = types.ModuleType("show_service.harmony")

    class Harmony:
        def __init__(self, publish, *, rate_hz=10.0):
            self.calls = []

        def tick(self, now_s, piano, audio_chroma=None):
            self.calls.append(piano)

    harmony.Harmony = Harmony

    receiver = types.ModuleType("show_service.receiver")

    class Receiver:
        def __init__(self, cfg, *, on_audio, on_midi, on_tick, clock=time.monotonic, sock=None):
            self.cfg, self.on_audio, self.on_midi, self.on_tick = cfg, on_audio, on_midi, on_tick
            self.started = self.stopped = False
            self.offsets = None

        def start(self):
            self.started = True

        def stop(self, timeout=2.0):
            self.stopped = True

        def health(self):
            return HEALTH

        def set_offsets(self, offsets):
            self.offsets = offsets

    receiver.Receiver = Receiver

    artnet = types.ModuleType("show_service.artnet_out")      # records what it is given; sends nothing

    class OutConfig:
        def __init__(self):
            self.target, self.patch_path, self.hue_latency_ms, self.room_dmx_url = ("env-host", 1), "p.yaml", 0.0, None

    class VirtualMaestro:
        def __init__(self, **kw):
            self.kw, self.started, self.stopped = kw, False, False

        def start(self):
            self.started = True

        def stop(self, timeout=2.0):
            self.stopped = True

        def status(self):
            return {"state": "sending" if self.kw["target"] else "off"}

    artnet.config_from_env = lambda env=None: OutConfig()
    artnet.VirtualMaestro = VirtualMaestro

    clock = types.ModuleType("show_service.clock")

    class Clock:
        def __init__(self, publish=None, **kw):
            self.publish, self.blocks, self.hits, self.ticks = publish, [], [], 0

        def process(self, stream, n0, t0_host_s, pcm, fs, flags):
            self.blocks.append((stream, n0, flags))

        def tick(self, now_s):
            self.ticks += 1

        def observe_hit(self, t_s, band, strength=1.0):
            self.hits.append((t_s, band, strength))

        def next_beat(self, t_s, lead_s=0.0, *, gate="beat"):
            return None

        def state(self):
            return {"clock": None, "stats": {"ticks": self.ticks}}

    clock.Clock = Clock
    for name, mod in (("dsp", dsp), ("midi", midi), ("harmony", harmony), ("receiver", receiver),
                      ("artnet_out", artnet), ("clock", clock)):
        monkeypatch.setitem(sys.modules, f"show_service.{name}", mod)


def test_wiring_with_fake_modules(monkeypatch):
    install_fakes(monkeypatch)
    show = main_mod.Show(OPTS)
    show.start()
    rx = show.rx
    try:
        assert rx.started and rx.cfg is OPTS.config
        assert show.modules == {"dsp": "ok", "midi": "ok", "harmony": "ok", "receiver": "ok",
                                "artnet_out": "ok", "clock": "ok"}
        vm = show.artnet
        assert vm.started and vm.kw["target"] is None                     # config's target (off) over the env's
        assert vm.kw["features_fn"] == show.features.latest and vm.kw["clock"] is show.clock
        assert vm.kw["patch_path"] == "p.yaml" and vm.kw["palette_fn"] is None
        pcm = np.zeros((128, 1), np.float32)
        rx.on_audio("room-mic", 0, 1.0, pcm, 48000, contract.AUDIO_SEGMENT)
        rx.on_audio("boom", 128, 1.0, pcm, 48000, 0)                       # raises inside: guarded
        assert show.features.blocks == 1 and show._g["features.process"].errors == 1
        # the clock gets the same blocks, after Features, even when Features failed on one
        assert show.clock.blocks == [("room-mic", 0, contract.AUDIO_SEGMENT), ("boom", 128, 0)]
        # Features' music.hit bodies reach the clock and the bus; other types only the bus
        show.features.publish({"type": contract.MUSIC_HIT,
                               "data": {"t_ms": 1500.0, "band": "bass", "strength": 0.8, "stream": "room-mic"}})
        show.features.publish({"type": contract.MUSIC_HIT, "data": {"band": "sub"}})      # malformed: guarded
        assert show.clock.hits == [(1.5, "bass", 0.8)] and show._g["clock.observe_hit"].errors == 1
        rx.on_midi("test-piano", "batch-a", 1.0, lambda t_ns: 2.5)
        rx.on_midi("room-piano", "batch-b", 1.0, lambda t_ns: 2.5)
        rx.on_midi("broken-piano", "batch-c", 1.0, lambda t_ns: 2.5)
        assert set(show.pianos) == {"test-piano", "room-piano", "broken-piano"}
        assert show.pianos["room-piano"].msgs == [("batch-b", 2.5)]
        assert show.pianos["room-piano"].state_channels == (1,)          # the kiosk sender's states: channel 1
        rx.on_tick(10.0)
        assert show.harmony.calls[-1] is show.pianos["room-piano"]           # live before test
        assert show._g["piano.tick"].errors == 1                            # the broken piano didn't stop the tick
        show.pianos["room-piano"].present = show.pianos["broken-piano"].present = False
        rx.on_tick(10.01)
        assert show.harmony.calls[-1] is show.pianos["test-piano"]
        show.pianos["test-piano"].present = False
        rx.on_tick(10.02)
        assert show.harmony.calls[-1] is None
        show.set_offsets({"room-mic": 3.0})
        assert rx.offsets == {"room-mic": 3.0}
        for _ in range(100):
            if show.bus.latest(contract.MUSIC_FEATURES):
                break
            time.sleep(0.02)
        st = show.state()
        assert st["active_audio"] == "room-mic" and st["streams"] == HEALTH["streams"]
        assert st["piano"]["present"] is True and st["piano"]["streams"]["room-piano"]["sess"] == "room-piano-1"
        assert st["features"]["energy"] == 0.5 and st["features"]["levels"] == {"sub": 0.2}
        assert st["features_stats"] == {"blocks": 1} and st["config"]["offsets_ms"] == {"room-mic": 3.0}
        assert st["errors"]["features.process"] == 1 and "clients" in st["bus"] and st["artnet"] == {"state": "off"}
        assert st["clock"] == {"clock": None, "stats": {"ticks": 1}} and show.clock.ticks == 3   # snapshot at 10.0
        base = f"http://127.0.0.1:{show.bus.port}"
        code, h = _get(base + "/healthz")
        assert code == 200 and h["ok"] and h["streams"] == {"room-mic": "streaming"}
        code, body = _get(base + "/api/v1/state")
        assert code == 200 and body["modules"]["receiver"] == "ok"
    finally:
        show.stop()
    assert rx.stopped and vm.stopped


def _batch(seq: int, ev: list, sess: str) -> vban.MidiBatch:
    return vban.parse_midi_events(vban.midi_events_obj(seq, ev, (), sess=sess))


def _state(seq: int, t: float, keys: dict, sess: str) -> vban.MidiState:
    k = [0] * 88
    for p, v in keys.items():
        k[p - 21] = v
    return vban.parse_midi_state(vban.midi_state_obj(seq, k, 0, 0, 0, int(t * 1e9), sess=sess))


def test_one_piano_voice_on_the_bus(monkeypatch):
    """room-piano and test-piano together (real midi.Piano): the bus carries one sess at a time, live before test,
    and an idle piano's 2 Hz states publish nothing."""
    install_fakes(monkeypatch)
    monkeypatch.setitem(sys.modules, "show_service.midi", real_midi)
    show = main_mod.Show(OPTS)
    show.start()
    out: list[dict] = []
    piano_types = (contract.MUSIC_NOTE, contract.MUSIC_PEDAL, contract.MUSIC_NOTES)
    show.publish = lambda m: out.append(m) if m["type"] in piano_types else None   # pianos are built lazily
    to_host = lambda t_ns: t_ns / 1e9                # noqa: E731 (the sender's clock is ours here)
    on = lambda t, p, v=80: [int(t * 1e9), 0x90, p, v]          # noqa: E731
    off = lambda t, p: [int(t * 1e9), 0x80, p, 0]               # noqa: E731
    try:
        seq = {"test-piano": 0, "room-piano": 0}

        def play(stream, ev, t):
            seq[stream] += 1
            show.on_midi(stream, _batch(seq[stream], ev, stream + "-wire"), t, to_host)

        def idle(stream, t, keys=None):
            show.on_midi(stream, _state(seq[stream], t, keys or {}, stream + "-wire"), t, to_host)

        def run(t0, t1, live_keys=None):
            t = t0
            while t < t1 - 1e-9:
                if abs(t * 2 - round(t * 2)) < 1e-6:     # both senders' 2 Hz states
                    idle("test-piano", t)
                    idle("room-piano", t, live_keys)
                show.on_tick(t)
                t = round(t + 0.01, 3)

        play("test-piano", [on(1.0, 40)], 1.0)
        play("test-piano", [off(1.1, 40)], 1.1)
        run(1.2, 30.0)                                # test-piano present ~10 s, then quiet; room-piano idle
        test_sess = show.pianos["test-piano"].sess
        assert {m["data"]["sess"] for m in out} == {test_sess}
        assert out[0]["type"] == contract.MUSIC_NOTE and out[0]["data"]["on"]       # the first strike itself
        n_quiet = len(out)
        assert out[-1]["type"] == contract.MUSIC_NOTES and out[-1]["data"]["present"] is False
        assert all(m["data"]["t_ms"] < 12_000 for m in out)                       # nothing after the flip frame
        play("room-piano", [on(30.0, 60, 90), on(30.0, 64, 90)], 30.0)           # live holds a chord ...
        run(30.01, 31.0, {60: 90, 64: 90})
        play("test-piano", [on(31.0, 41)], 31.0)                                   # ... while test plays too
        run(31.01, 34.0, {60: 90, 64: 90})
        live = [m for m in out[n_quiet:]]
        room_sess = show.pianos["room-piano"].sess
        assert {m["data"]["sess"] for m in live} == {room_sess}                    # one sess while live plays
        assert live[0]["type"] == contract.MUSIC_NOTE and live[0]["data"]["pitch"] == 60
        assert sum(1 for m in live if m["type"] == contract.MUSIC_NOTE and m["data"]["on"]) == 2   # no re-strikes
        st = show.state()
        assert st["piano"]["voice"] == "room-piano" and st["piano"]["streams"]["test-piano"]["voice"] is False
        assert show._voice is show.pianos["room-piano"] and show.pianos["test-piano"].voice is False
        # live lets go and goes idle; 10 s later test plays: one hand-over, to test's sess
        play("room-piano", [off(34.0, 60), off(34.0, 64)], 34.0)
        run(34.01, 45.0)
        n = len(out)
        play("test-piano", [on(45.0, 43)], 45.0)
        run(45.01, 46.0)
        assert {m["data"]["sess"] for m in out[n:]} == {test_sess}
        sesses = [m["data"]["sess"] for m in out]
        assert sum(1 for a, b in zip(sesses, sesses[1:]) if a != b) == 2           # test -> live -> test
        assert show._g["on_midi"].errors == 0 and show._g["piano.tick"].errors == 0
    finally:
        show.stop()


def test_artnet_target_from_config(monkeypatch):
    install_fakes(monkeypatch)
    opts = config.Options(config=OPTS.config, artnet_target=("artnet-node.lan", 6454))
    show = main_mod.Show(opts)
    show.start()
    try:
        assert show.artnet.kw["target"] == ("artnet-node.lan", 6454)   # a fake: nothing is sent
        assert show.state()["artnet"] == {"state": "sending"}
    finally:
        show.stop()


def test_degrades_when_every_module_is_missing(monkeypatch):
    for name in ("dsp", "midi", "harmony", "receiver", "artnet_out", "clock"):
        monkeypatch.setitem(sys.modules, f"show_service.{name}", None)
    show = main_mod.Show(OPTS)
    show.start()
    try:
        assert show.rx is None and show.features is None and show.harmony is None and show.artnet is None
        assert all(v.startswith("missing") for v in show.modules.values()) and len(show.modules) == 6
        show.on_midi("room-piano", "x", 1.0, lambda t: 0.0)                 # no midi module: ignored
        show.on_tick(1.0)
        show.on_audio("room-mic", 0, 1.0, np.zeros((4, 1), np.float32), 48000, 0)
        show.set_offsets({})
        base = f"http://127.0.0.1:{show.bus.port}"
        assert _get(base + "/healthz")[0] == 200
        code, body = _get(base + "/api/v1/state")
        assert code == 200 and body["streams"] == {} and body["piano"]["present"] is False
    finally:
        show.stop()


def test_constructor_failure_is_logged_not_fatal(monkeypatch):
    install_fakes(monkeypatch)
    bad = types.ModuleType("show_service.harmony")

    class Harmony:
        def __init__(self, publish, *, rate_hz=10.0):
            raise TypeError("wrong interface")

    bad.Harmony = Harmony
    monkeypatch.setitem(sys.modules, "show_service.harmony", bad)
    show = main_mod.Show(OPTS)
    show.start()
    try:
        assert show.harmony is None and show.modules["harmony"].startswith("failed")
        show.rx.on_tick(1.0)
    finally:
        show.stop()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_python_m_show_service_starts_and_stops(tmp_path):
    port = _free_port()
    assert port != contract.BUS_PORT
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHOW_")}
    env["PYTHONUNBUFFERED"] = "1"
    log_path = tmp_path / "show.log"
    with open(log_path, "wb") as log:
        p = subprocess.Popen([sys.executable, "-m", "show_service", "--port", str(port), "--host", "127.0.0.1",
                              "--vban-bind", "127.0.0.1:0", "--latency-file", str(tmp_path / "latency.json")],
                             cwd=str(contract.REPO_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            up = False
            while time.monotonic() < deadline and p.poll() is None:
                try:
                    code, h = _get(f"http://127.0.0.1:{port}/healthz", timeout=1)
                    up = code == 200 and h["service"] == "show_service"
                except OSError:
                    up = False
                if up:
                    break
                time.sleep(0.2)
            assert up, log_path.read_text()
            code, body = _get(f"http://127.0.0.1:{port}/api/v1/state")
            assert code in (200, 503), body
            p.send_signal(signal.SIGTERM)
            assert p.wait(timeout=20) == 0, log_path.read_text()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    assert "stopped" in log_path.read_text()
