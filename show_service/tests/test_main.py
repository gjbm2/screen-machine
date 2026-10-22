"""show_service/__main__.py at the "does it crash" level: the wiring with fake modules (README interfaces), the
degraded start with every module missing, and ``python -m show_service`` up, answering /healthz, and out on SIGTERM.
Every bind is 127.0.0.1 on an ephemeral port; no senders, so nothing is sent anywhere."""

from __future__ import annotations

import asyncio
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
        def __init__(self, *, publish=None, hop=256, publish_hz=25.0, batch=True):
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
        def __init__(self, cfg, *, on_audio, on_midi, on_tick, clock=time.monotonic, sock=None, on_level=None,
                     coalesce=True, coalesce_min=1, kernel_stamps=True, split_midi=True):
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


def test_wiring_with_fake_modules(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    show_file = tmp_path / "show.yaml"                    # the MIDI state channels come from show.yaml (--no-brain:
    show_file.write_text("situations:\n  midi: {channels: [1]}\n")   # read once at start), never a code constant
    show = main_mod.Show(config.Options(config=OPTS.config, show_file=show_file))
    show.start()
    rx = show.rx
    try:
        assert rx.started and rx.cfg is OPTS.config
        assert show.modules == {"dsp": "ok", "midi": "ok", "harmony": "ok", "receiver": "ok",
                                "artnet_out": "ok", "clock": "ok", "mic": "ok"}
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
        assert show.pianos["room-piano"].state_channels == (1,)          # show.yaml situations.midi.channels
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
        assert show.modules.get("mic") == "ok"                               # (mic.py itself is there)
        missing = {k: v for k, v in show.modules.items() if k != "mic"}
        assert all(v.startswith("missing") for v in missing.values()) and len(missing) == 6
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
                              "--vban-bind", "127.0.0.1:0", "--latency-file", str(tmp_path / "latency.json"),
                              "--no-brain"],
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
            if code == 200:
                assert body["brain"] is None and "api" not in body["modules"]       # --no-brain: as before Stage 3
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/vibe", data=b"{}", method="PUT")
            try:
                urllib.request.urlopen(req, timeout=2)
                code = 200
            except urllib.error.HTTPError as e:
                code = e.code
            assert code == 501                                                       # bus.py's stub: no routes
            p.send_signal(signal.SIGTERM)
            assert p.wait(timeout=20) == 0, log_path.read_text()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    assert "stopped" in log_path.read_text()


def test_python_m_show_service_with_the_brain(tmp_path):
    """The real process with the brain on, every path a tmp one: HOME is tmp_path (so contract's state, LLM ledger and
    test-music paths are too), show.yaml names a room on a dead ephemeral port and no senders. Up, the Stage-3 routes
    answer, state.json is written in --state-dir at SIGTERM, exit 0."""
    port, dead = _free_port(), _free_port()
    assert contract.BUS_PORT not in (port, dead) and 8700 not in (port, dead)
    show_yaml = tmp_path / "show.yaml"
    show_yaml.write_text(f"version: 1\nroom:\n  url: http://127.0.0.1:{dead}\n")
    state_dir = tmp_path / "state"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHOW_")}
    env.update(PYTHONUNBUFFERED="1", HOME=str(tmp_path), OPENAI_API_KEY="")
    log_path = tmp_path / "show.log"
    with open(log_path, "wb") as log:
        p = subprocess.Popen([sys.executable, "-m", "show_service", "--port", str(port), "--host", "127.0.0.1",
                              "--vban-bind", "127.0.0.1:0", "--latency-file", str(tmp_path / "latency.json"),
                              "--show-file", str(show_yaml), "--state-dir", str(state_dir)],
                             cwd=str(contract.REPO_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 60
            card = None
            while time.monotonic() < deadline and p.poll() is None:
                try:
                    code, card = _get(f"http://127.0.0.1:{port}/api/v1/card", timeout=1)
                    if code == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.2)
            assert card and "hearing" in card, log_path.read_text()
            assert card["vibe"]["name"] is None and card["auto"]["state"] in ("blind", "off")   # no room: blind
            code, body = _get(f"http://127.0.0.1:{port}/api/v1/state")
            assert code == 200 and body["brain"]["running"] is True, body.get("brain")
            assert set(body["brain"]["components"]) == {"room", "situations", "autopilot", "sources", "recorder",
                                                        "ml", "structure", "listener", "cues", "director", "rig"}
            assert body["ml"]["state"] in ("failed", "backoff"), body["ml"]   # HOME is tmp: no ML venv there
            # Stage 5: the rig reads the real fixtures.yaml (read only): six fixtures, every output off, no problems
            rig = body["rig"]
            assert rig["enabled"] is True and rig["registry"]["counts"]["fixtures"] == 6, rig["registry"]
            assert rig["registry"]["problems"] == [] and rig["registry"]["error"] is None, rig["registry"]
            assert {o["state"] for o in rig["outputs"].values()} == {"off"}, rig["outputs"]
            assert rig["hue"]["state"] == "off" and not [t for t in body["threads"] if t.startswith("show-out-")]
            code, hs = _get(f"http://127.0.0.1:{port}/api/v1/hue/status")
            assert code == 200 and hs["state"] == "idle" and "fixtures.yaml" in hs["detail"], hs   # 68's four words
            assert body["brain"]["components"]["director"]["patch"]["from"] == "fixtures.yaml"
            assert body["brain"]["components"]["structure"]["context"] is None or isinstance(
                body["brain"]["components"]["structure"]["context"], dict)
            assert all(v == "ok" for v in body["modules"].values()), body["modules"]
            assert body["brain"]["settings"]["error"] is None and body["brain"]["state_file"]["path"].startswith(
                str(state_dir))
            p.send_signal(signal.SIGTERM)
            assert p.wait(timeout=20) == 0, log_path.read_text()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    saved = json.loads((state_dir / "state.json").read_text())
    assert saved["v"] == 1 and saved["director"]["target"] is None and saved["listener"] == {"on": False,
                                                                                             "set_at": None,
                                                                                             "mode": False}
    assert "stopped" in log_path.read_text() and "Traceback" not in log_path.read_text(), log_path.read_text()


# -- Stage 3: the brain's wiring (README S3.4), with fake components and the real brain.py and api.py ---------------
CALLS: list[str] = []


class FakeComp:
    def __init__(self, name: str, hz: float = 0.0, kw: dict | None = None, provides: dict | None = None) -> None:
        self.name, self.tick_hz, self.kw, self._prov = name, hz, kw or {}, provides or {}
        self.ticks = 0
        self.stopped = False

    def provides(self):
        return self._prov

    def configure(self, settings):
        self.settings = settings

    def restore(self, saved):
        pass

    def start(self):
        pass

    def tick(self, now_s):
        self.ticks += 1

    def snapshot(self):
        return {"ticks": self.ticks}

    def persist(self):
        return None

    def stop(self):
        self.stopped = True
        CALLS.append("stop:" + self.name)


class FakeDirector(FakeComp):
    def palette_fn(self, t_s):
        return {"*": (0.1, 0.2, 0.3)}


class FakeRig(FakeComp):
    """outputs.Rig's seams (Stage 5): set_look (ctx.set_look_out's fan-out) and on_hit (show-rx's hit tap)."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.looks, self.hits = [], []
        self.fail_hit = False

    def set_look(self, *, hue_depth=None, beat_lock=None, reset=False):
        self.looks.append((hue_depth, beat_lock, reset))

    def on_hit(self, d):
        if self.fail_hit:
            raise RuntimeError("rig hit")
        self.hits.append(d)

    def snapshot(self):
        return {"enabled": True, "outputs": {}}


class FakeML(FakeComp):
    """mllink.MLLink's shape: its stop() is a coroutine (brain.py awaits it)."""

    def stop(self):
        async def _stop():
            await asyncio.sleep(0.01)
            self.stopped = True
            CALLS.append("stop:" + self.name)
        return _stop()


def install_brain_fakes(monkeypatch) -> dict:
    """ring, situations (NoteTap), autopilot, sources, listener and director as fakes; brain.py and api.py real."""
    made: dict = {}
    ring = types.ModuleType("show_service.ring")

    class Rings:
        def __init__(self):
            self.writes = []

        def write(self, stream, n0, t0_host_s, pcm, fs, flags):
            if stream == "boom":
                raise RuntimeError("ring")
            self.writes.append((stream, n0, flags))
            CALLS.append("ring.write")

        def info(self):
            return {"writes": len(self.writes)}

    ring.Rings = Rings
    sit = types.ModuleType("show_service.situations")

    class NoteTap:
        def __init__(self):
            self.msgs = []

        def on_midi(self, stream, msg, arrival_s, to_host):
            self.msgs.append((stream, msg))
            CALLS.append("notetap")

        def raw_since(self, t_s):
            return []

        def stats(self):
            return {"batches": len(self.msgs)}

    sit.NoteTap = NoteTap

    def module(name, build):
        m = types.ModuleType(f"show_service.{name}")

        def attach(ctx, **kw):
            got = build(ctx, kw)
            made[name] = got
            return got
        m.attach = attach
        monkeypatch.setitem(sys.modules, f"show_service.{name}", m)

    module("autopilot", lambda ctx, kw: [FakeComp("room", 0.0, kw, {"room": lambda: {"current": "gallery"}}),
                                         FakeComp("situations", 1.0), FakeComp("autopilot", 1.0, kw)])
    module("sources", lambda ctx, kw: FakeComp("sources", 1.0, kw))
    module("mllink", lambda ctx, kw: FakeML("ml", 1.0, kw, {"ml": lambda: {"state": "disabled"}}))
    module("structure", lambda ctx, kw: FakeComp("structure", 10.0, kw, {"music_context": lambda: None,
                                                                         "on_music_event": "not-a-list-slot"}))

    class MLFeed:                                        # mllink.MLFeed: show-rx's writer
        def __init__(self):
            self.writes = []

        def write(self, stream, n0, t0_host_s, pcm, fs, flags):
            if stream == "boom":
                raise RuntimeError("feed")
            self.writes.append((stream, n0, flags))
            CALLS.append("ml.feed")
    sys.modules["show_service.mllink"].MLFeed = MLFeed
    module("listener", lambda ctx, kw: FakeComp("listener", 1.0, kw, {"beliefs": lambda: None}))
    module("cues", lambda ctx, kw: FakeComp("cues", 10.0, kw, {"cue_sheet": lambda: []}))   # README S5.22
    module("director", lambda ctx, kw: FakeDirector("director", 10.0, kw, {"vibe": lambda: None}))
    module("outputs", lambda ctx, kw: FakeRig("rig", 1.0, kw, {"rig": lambda: None}))
    monkeypatch.setitem(sys.modules, "show_service.ring", ring)
    monkeypatch.setitem(sys.modules, "show_service.situations", sit)
    return made


def _brain_opts(tmp_path, **kw) -> config.Options:
    show = tmp_path / "show.yaml"
    show.write_text("version: 1\n")
    return config.Options(config=contract.Config(bus_bind=("127.0.0.1", 0), vban_bind=("127.0.0.1", 0),
                                                 senders=(contract.Sender("cli", "localhost", 7000, ("x",)),)),
                          brain=True, show_file=show, state_dir=tmp_path / "state",
                          vibes_file=tmp_path / "vibes.yaml", **kw)


def _with_seams(monkeypatch) -> None:
    """The fake Receiver and VirtualMaestro gain the Stage-3 seams (set_senders/set_priority, set_look,
    section_change), recording what they get."""
    rx_mod, vm_mod = sys.modules["show_service.receiver"], sys.modules["show_service.artnet_out"]

    class Receiver(rx_mod.Receiver):
        def set_senders(self, senders):
            self.senders = senders

        def set_priority(self, names):
            self.priority = names

    class VirtualMaestro(vm_mod.VirtualMaestro):
        def set_look(self, *, hue_depth=None, beat_lock=None, reset=False, plan=None):
            self.look = (hue_depth, beat_lock, reset)
            self.plan_seen = plan

        def section_change(self):
            self.sections = getattr(self, "sections", 0) + 1

        def stop(self, timeout=2.0):
            CALLS.append("artnet.stop")
            super().stop(timeout)

    monkeypatch.setattr(rx_mod, "Receiver", Receiver)
    monkeypatch.setattr(vm_mod, "VirtualMaestro", VirtualMaestro)


def test_brain_wiring_with_fake_components(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    made = install_brain_fakes(monkeypatch)
    CALLS.clear()
    opts = _brain_opts(tmp_path)
    show = main_mod.Show(opts, brain_kw={"listener": {"llm": "fake-llm"}})
    show.start()
    rx, vm, ctx = show.rx, show.artnet, show.ctx
    try:
        assert all(v == "ok" for v in show.modules.values()), show.modules
        assert {"api", "ring", "situations", "brain", "autopilot", "sources", "listener", "director"} <= set(show.modules)
        assert show.brain.running and list(show.brain.components) == ["room", "situations", "autopilot", "sources",
                                                                      "recorder", "ml", "structure", "listener",
                                                                      "cues", "director", "rig"]
        # files come from the options, test kwargs on top
        sd = tmp_path / "state"
        assert made["director"].kw == {"log_path": sd / "director.jsonl", "vibes_path": tmp_path / "vibes.yaml",
                                       "patch_path": __import__("pathlib").Path("p.yaml")}
        assert made["listener"].kw == {"log_path": sd / "listener.jsonl", "llm": "fake-llm",
                                       "clips_dir": sd / "listen" / "clips"}
        assert made["cues"].kw == {"log_path": sd / "cues.jsonl"} and ctx.cue_sheet() == []
        assert made["sources"].kw == {"log_dir": sd} and made["autopilot"][2].kw == {"log_path": sd / "autopilot.jsonl"}
        assert made["mllink"].kw == {"log_path": sd / "ml-worker.log", "child_pids": show._child_pids}
        assert made["structure"].kw == {} and show._child_pids() == []           # the fake clock has no beat process
        # Stage 5: the rig, after the director, fed by reference (the fake clock has no snapshot: no clock)
        rk = made["outputs"].kw
        assert rk["palette_fn"] == show.director.palette_fn and rk["look_fn"] is None and rk["clock"] is None
        assert rk["features_fn"] == show.features.latest and rk["neutral"] is None        # the fake artnet_out
        assert rk["latency_path"] == opts.config.latency_file and rk["patch_path"] == __import__("pathlib").Path("p.yaml")
        assert rk["driver_kw"] == {"artnet": {"state_file": sd / "nodes.json"},
                                   "hue": {"patch_path": __import__("pathlib").Path("p.yaml")}}
        assert show.rig is made["outputs"] and ctx.rig() is None
        assert show.brain.store.path == sd / "state.json"
        # the engine side of the context
        assert ctx.features == show.features.latest and ctx.health == show.health and ctx.notes is show.tap
        assert ctx.rings is show.rings and ctx.set_senders == rx.set_senders and ctx.set_priority == rx.set_priority
        assert ctx.static_senders == opts.config.senders and ctx.notify == show.api.notify
        assert ctx.set_look_out == show._set_look_out and ctx.section_change == vm.section_change
        assert ctx.piano() == main_mod.NEUTRAL_PIANO_VIEW and ctx.room() == {"current": "gallery"}
        # Stage 4: the feed is the context's, the components' slots are filled (a list slot is appended to)
        assert ctx.ml_feed is show.mlfeed and ctx.ml() == {"state": "disabled"} and ctx.music_context() is None
        assert ctx.on_music_event == ["not-a-list-slot"] and show.ml is made["mllink"]
        assert ctx.rhythm is None and ctx.clock_correct is None          # the fake dsp and clock have neither
        ctx.set_look_out(hue_depth=0.4, beat_lock=False)
        ctx.set_gate_ref(-35.0)
        assert vm.look == (0.4, False, False) and show.features.gate_ref_lufs == -35.0
        assert show.rig.looks == [(0.4, False, False)]                    # the fan-out: Art-Net out, then the rig
        ctx.set_look_out(reset=True)
        assert vm.look == (None, None, True) and show.rig.looks[-1] == (None, None, True)
        n = len(show.rig.looks)                                          # README S5.18: the composer's plan is
        ctx.set_look_out(plan={"depth": 0.5, "middle_depth": 0.5, "lamps": {"*": (1.0, 1.0)}})   # Art-Net out's alone
        assert vm.plan_seen == {"depth": 0.5, "middle_depth": 0.5, "lamps": {"*": (1.0, 1.0)}}
        assert len(show.rig.looks) == n and show._g["rig.set_look"].errors == 0
        # the hit tap: the clock, then the rig; a raising rig is guarded
        hit = {"type": contract.MUSIC_HIT, "data": {"t_ms": 1000.0, "band": "sub", "strength": 0.8}}
        show._features_publish(hit)
        assert show.clock.hits[-1] == (1.0, "sub", 0.8) and show.rig.hits == [hit["data"]]
        show.rig.fail_hit = True
        show._features_publish(hit)
        assert show._g["rig.hit"].errors == 1 and len(show.clock.hits) == 2
        show.rig.fail_hit = False
        # Art-Net's palette is the director's
        assert vm.kw["palette_fn"] == show.director.palette_fn and vm.kw["palette_fn"](1.0) == {"*": (0.1, 0.2, 0.3)}
        # show-rx taps: the ring after Features and the Clock; the note tap before the piano
        pcm = np.zeros((128, 1), np.float32)
        rx.on_audio("room-mic", 0, 1.0, pcm, 48000, contract.AUDIO_SEGMENT)
        rx.on_audio("boom", 128, 1.0, pcm, 48000, 0)                    # Features and the ring raise: guarded
        assert show.rings.writes == [("room-mic", 0, contract.AUDIO_SEGMENT)] and show._g["ring.write"].errors == 1
        assert show.mlfeed.writes == [("room-mic", 0, contract.AUDIO_SEGMENT)] and show._g["ml.feed"].errors == 1
        assert CALLS.index("ring.write") < CALLS.index("ml.feed")       # the ML feed after the ring
        assert show.clock.blocks[-1] == ("boom", 128, 0)
        rx.on_midi("room-piano", "batch-a", 1.0, lambda t_ns: 2.5)
        assert show.tap.msgs == [("room-piano", "batch-a")] and show.pianos["room-piano"].msgs == [("batch-a", 2.5)]
        rx.on_tick(10.0)
        assert show._pview["present"] is True and show._pview["stream"] == "room-piano"
        # the API answers once the brain runs; the state carries the brain
        base = f"http://127.0.0.1:{show.bus.port}"
        code, card = _get(base + "/api/v1/card")
        assert code == 200 and card["vibe"] == {"name": None, "label": "", "known": False}
        code, st = _get(base + "/api/v1/state")
        assert code == 200 and st["brain"]["running"] is True and set(st["brain"]["components"]) == set(
            show.brain.components)
        assert st["brain"]["ring"] == {"writes": 1} and st["brain"]["notes"] == {"batches": 1}
        assert st["brain"]["modules"]["director"] == "ok"
        assert st["api"]["events"]["clients"] == 0 and "card_errors" in st["api"]["events"]   # the card socket
        assert set(st["ml"]) == {"ticks"}                                    # the MLLink's snapshot
        assert st["rig"] == {"enabled": True, "outputs": {}}                 # the Rig's snapshot
        assert st["rx_ms"]["on_audio"]["n"] == 2 and st["rx_ms"]["on_tick"]["n"] >= 1
        # the mic on demand (README S5.16): the ring, the card's refresh and the room view reach it
        assert show.mic._rings is show.rings and show.mic._notify == show.api.notify
        assert show.mic._mode_label() is None and card["mic_state"]["on"] is False
    finally:
        show.stop()
    assert CALLS.index("stop:director") < CALLS.index("artnet.stop")      # the brain stops before Art-Net
    assert [c for c in CALLS if c.startswith("stop:")] == ["stop:rig", "stop:director", "stop:cues",
                                                          "stop:listener", "stop:structure",
                                                          "stop:ml", "stop:sources", "stop:autopilot",
                                                          "stop:situations", "stop:room"]   # ml's stop awaited
    assert (tmp_path / "state" / "state.json").exists()


def test_a_missing_stage3_module_is_left_out(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    install_brain_fakes(monkeypatch)
    monkeypatch.setitem(sys.modules, "show_service.listener", None)
    monkeypatch.setitem(sys.modules, "show_service.director", None)
    show = main_mod.Show(_brain_opts(tmp_path))
    show.start()
    try:
        assert show.modules["listener"].startswith("missing") and show.modules["director"].startswith("missing")
        assert show.director is None and show.artnet.kw["palette_fn"] is None        # Art-Net: the fixed colour
        assert show.rig is not None and show.rig.kw["palette_fn"] is None and show.rig.kw["look_fn"] is None
        assert show.brain.running and "listener" not in show.brain.components
        code, card = _get(f"http://127.0.0.1:{show.bus.port}/api/v1/card")
        assert code == 200 and card["listening"]["why"] == "The listener isn't running."
    finally:
        show.stop()


def test_a_missing_outputs_module_is_exactly_stage4(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    install_brain_fakes(monkeypatch)
    monkeypatch.setitem(sys.modules, "show_service.outputs", None)
    show = main_mod.Show(_brain_opts(tmp_path))
    show.start()
    try:
        assert show.modules["outputs"].startswith("missing") and show.rig is None
        assert "rig" not in show.brain.components and show.ctx.rig is None
        show.ctx.set_look_out(hue_depth=0.2, beat_lock=True)
        assert show.artnet.look == (0.2, True, False)                              # Art-Net out alone
        show._features_publish({"type": contract.MUSIC_HIT, "data": {"t_ms": 5.0, "band": "sub", "strength": 1.0}})
        assert show._g["rig.hit"].errors == 0 and show.state()["rig"] is None
    finally:
        show.stop()


def test_no_brain_builds_no_brain(monkeypatch, tmp_path):
    install_fakes(monkeypatch)
    install_brain_fakes(monkeypatch)
    opts = _brain_opts(tmp_path)
    show = main_mod.Show(config.Options(config=opts.config, brain=False, state_dir=tmp_path / "state",
                                        show_file=tmp_path / "missing.yaml"))
    show.start()
    try:
        assert show.brain is None and show.rings is None and show.tap is None and show.api is None
        assert show.rig is None and show.state()["rig"] is None                       # no brain: no rig (Stage 5)
        assert not any(k in show.modules for k in ("api", "brain", "ring", "director"))
        assert show.state()["brain"] is None and show.state()["api"] is None
        show.on_midi("room-piano", "batch-a", 1.0, lambda t_ns: 2.5)
        assert show.pianos["room-piano"].state_channels is None         # no show.yaml: every channel
    finally:
        show.stop()
    assert not (tmp_path / "state").exists()


def test_piano_view_from_the_voice_piano_and_the_note_tap(monkeypatch, tmp_path):
    """The real midi.Piano and situations.NoteTap: PianoView's present, keys, damper, rate and velocity."""
    import importlib
    real_situations = importlib.import_module("show_service.situations")      # before the fakes replace it
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    install_brain_fakes(monkeypatch)
    monkeypatch.setitem(sys.modules, "show_service.midi", real_midi)
    monkeypatch.setitem(sys.modules, "show_service.situations", real_situations)
    show = main_mod.Show(_brain_opts(tmp_path))
    show.start()
    show.publish = lambda m: None
    to_host = lambda t_ns: t_ns / 1e9                # noqa: E731
    try:
        seq = 0
        for k in range(8):                           # 8 strikes, 0.25 s apart, velocity 64 then 127
            seq += 1
            t = 1.0 + 0.25 * k
            show.on_midi("test-piano", _batch(seq, [[int(t * 1e9), 0x90, 60 + k, 64 if k < 4 else 127]], "s1"),
                         t, to_host)
            show.on_tick(t)
        seq += 1
        show.on_midi("test-piano", _batch(seq, [[int(3.0e9), 0xB0, 64, 127]], "s1"), 3.0, to_host)   # damper down
        show.on_tick(3.0)
        pv = show.ctx.piano()
        assert pv["present"] is True and pv["stream"] == "test-piano" and pv["source"] == "test"
        assert pv["keys_down"] == 8 and pv["damper"] == 1.0 and 0.2 <= pv["last_note_age_s"] <= 0.3
        assert pv["notes_per_s"] == 2.0 and 0.5 < pv["velocity"] <= 1.0
        assert show.tap.stats()["notes"] == 8
    finally:
        show.stop()


def test_the_mic_decides_before_the_receiver_starts_and_its_routes_answer(monkeypatch, tmp_path):
    """README S5.16 without the brain: the mode claim alone decides, before show-rx's first SUBSCRIBE."""
    install_fakes(monkeypatch)
    rx_mod = sys.modules["show_service.receiver"]
    seen: list = []
    health = {"active_audio": None, "senders": {"kiosk": {"streams": ["room-mic", "room-piano"], "on_demand": False}},
              "streams": {}}

    class Receiver(rx_mod.Receiver):
        def health(self):
            return health

        def set_off_streams(self, names, covered=None):
            seen.append((sorted(names), self.started, sorted(covered or ())))

    monkeypatch.setattr(rx_mod, "Receiver", Receiver)
    show_file = tmp_path / "show.yaml"
    show_file.write_text("senders:\n  kiosk: {hosts: [k], streams: [room-mic, room-piano]}\n")
    sd = tmp_path / "state"
    show = main_mod.Show(config.Options(config=OPTS.config, show_file=show_file, state_dir=sd))
    show.start()
    try:
        assert seen[0] == (["room-mic"], False, ["room-mic", "room-piano"])   # off, decided before show-rx started
        assert show._engine()["mic_state"]["why"] == "Off: nothing needs it" and show.modules["mic"] == "ok"
        base = f"http://127.0.0.1:{show.bus.port}"
        code, b = _get(base + "/api/v1/mic")
        assert code == 200 and b["on"] is False and b["streams"] == {"capturing": [], "off": ["room-mic"]}
        req = urllib.request.Request(base + "/api/v1/mic", data=json.dumps({"mode": True}).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            b = json.loads(r.read())
        assert b["on"] is True and b["why"] == "Listening: this mode follows the music" and seen[-1][:2] == ([], True)
        assert json.loads((sd / "mic.json").read_text()) == {"v": 1, "muted": False, "mode": True}
        code, st = _get(base + "/api/v1/state")
        assert code == 200 and st["mic"]["on"] is True and set(st["mic"]["receiver"]) >= {"off", "off_held"}
        assert {t.name for t in __import__("threading").enumerate()} >= {"show-mic"}
    finally:
        show.stop()
    assert not show.mic._thread
