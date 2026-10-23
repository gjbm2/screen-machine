"""show_sender: MIDI parsing and batching, audio counters over overflows, SUBSCRIBE, and a UDP loopback of the
test piano, every packet checked with show_service/vban.py's own parser."""
from __future__ import annotations

import io
import socket
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import show_sender as ss  # noqa: E402
from show_sender import vban  # noqa: E402


class Sink:
    def __init__(self) -> None:
        self.packets: list[tuple[str, bytes]] = []

    def __call__(self, stream: str, packet: bytes) -> bool:
        self.packets.append((stream, packet))
        return True

    def parsed(self):
        return [vban.parse(p) for _, p in self.packets]


def test_midi_parser_running_status_realtime_sysex():
    p = ss.MidiParser()
    data = bytes([0x90, 60, 100, 64, 90, 0xF8, 67, 0,      # on C4, on E4 (running), clock, E... G4 vel 0 = off
                  0xF0, 1, 2, 3, 0xF7,                    # sysex ignored
                  0xB0, 64, 127, 7, 100,                  # damper down; CC7 dropped
                  0x81, 60, 40, 0xC0, 5])                 # off ch2; program change dropped
    assert p.feed(data) == [(0x90, 60, 100), (0x90, 64, 90), (0x80, 67, 0), (0xB0, 64, 127), (0x81, 60, 40)]
    assert p.feed(bytes([62, 50])) == []                 # running status after a program change: 1 data byte each


def test_midi_batches_prev_and_state():
    sink = Sink()
    ms = ss.MidiStream("room-piano", sink, sess="s-test")
    for i in range(4):
        ms.events([(1000 + i, 0x90, 60 + i, 80)])
    ms.events([(2000, 0x80, 60, 0), (2001, 0xB0, 64, 127), (2002, 0x91, 70, 99)])   # ch2 doesn't touch the state
    ms.state(3000)
    pk = sink.parsed()
    batches = [vban.parse_midi_events(p) for p in pk[:5]]
    assert [b.seq for b in batches] == [1, 2, 3, 4, 5] and all(b.sess == "s-test" for b in batches)
    assert [s for s, _ in batches[4].prev] == [4, 3]                                # newest first, two of them
    assert [p.counter for p in pk] == list(range(6))
    st = vban.parse_midi_state(pk[5])
    assert st.seq == 5 and st.cc64 == 127
    assert st.keys[60 - 21] == 0 and st.keys[61 - 21] == 80 and st.keys[70 - 21] == 0


def test_midi_batches_split_at_the_max():
    sink = Sink()
    ms = ss.MidiStream("room-piano", sink, sess="s")
    ms.events([(i, 0x90, 40 + i % 40, 64) for i in range(vban.MIDI_BATCH_MAX + 5)])
    b = [vban.parse_midi_events(p) for p in sink.parsed()]
    assert [len(x.ev) for x in b] == [vban.MIDI_BATCH_MAX, 5]


def test_audio_counters_skip_over_overflows_and_heartbeat():
    sink = Sink()
    au = ss.AudioStream("room-mic", sink, channels=1, dev="ATR4697")
    f = au.frames
    pcm = np.full((f, 1), 0.5, np.float32)
    for k in range(3):
        au.packet(pcm, 10_000_000 + k * f * 20833)
    au.overflow(0.010)                                     # ~10 ms lost: ~4 packets skipped, one xrun
    au.packet(pcm, 99)
    au.heartbeat("ok", 10.7)
    pk = sink.parsed()
    audio = [p for p in pk if isinstance(p, vban.AudioPacket)]
    skipped = int(round(0.010 * ss.FS / f))
    assert [p.counter for p in audio] == [0, 1, 2, 3 + skipped]
    hb = vban.parse_heartbeat(pk[-1])
    assert hb.frame == 3 + skipped and hb.n == (3 + skipped) * f and hb.xruns == 1 and hb.dev == "ATR4697"
    assert hb.peak_dbfs[0] == pytest.approx(-6.0, abs=0.1)
    assert pk[-1].counter == 0                             # the heartbeat's own TEXT counter
    sess = au.sess
    au.overflow(0.4)                                       # over 250 ms: a new sess, counters from 0
    assert au.sess != sess and au.counter == 0 and au.n == 0


def test_subscriptions_expire_and_filter_streams():
    subs = ss.Subscriptions()
    assert subs.on_packet(vban.subscribe_packet(["room-mic"], 0), ("10.0.0.5", 6980), now=100.0)
    assert subs.target("room-mic", 101.0) == ("10.0.0.5", 6980)
    assert subs.target("room-piano", 101.0) is None
    assert subs.target("room-mic", 100.0 + ss.SUB_TTL_S + 0.1) is None
    assert not subs.on_packet(b"junk", ("x", 1), 0.0)


def test_card_matching(tmp_path, monkeypatch):
    cards = (" 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n                      HDA Intel PCH at 0x6001120000\n"
             " 1 [Piano          ]: USB-Audio - adsilent\n                      adsilent at usb-0000:00:14.0-2\n"
             " 2 [ATR4697USB     ]: USB-Audio - AT2005USB / ATR4697-USB\n")
    real_read = ss.pathlib.Path.read_text
    monkeypatch.setattr(ss.pathlib.Path, "read_text",
                        lambda self, *a, **k: cards if str(self) == "/proc/asound/cards" else real_read(self, *a, **k))
    monkeypatch.setattr(ss.glob, "glob", lambda pat: [pat] if pat in ("/dev/snd/midiC1D0", "/dev/snd/pcmC2D0c") else [])
    assert ss.find_card("adsilent", "/dev/snd/midiC{n}D0") == (1, "Piano")
    assert ss.find_card(None, "/dev/snd/midiC{n}D0") == (1, "Piano")
    assert ss.find_card(None, "/dev/snd/pcmC{n}D0c") == (2, "ATR4697USB")
    assert ss.find_card("nothing", "/dev/snd/pcmC{n}D0c") is None


def test_device_choices_and_the_receivers_hint(monkeypatch):
    cards = (" 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
             " 1 [ATR4697USB     ]: USB-Audio - AT2005USB / ATR4697-USB\n"
             " 2 [Webcam         ]: USB-Audio - USB Webcam ATR4697USB-like\n")
    real_read = ss.pathlib.Path.read_text
    monkeypatch.setattr(ss.pathlib.Path, "read_text",
                        lambda self, *a, **k: cards if str(self) == "/proc/asound/cards" else real_read(self, *a, **k))
    monkeypatch.setattr(ss.glob, "glob", lambda pat: [pat] if pat in ("/dev/snd/pcmC1D0c", "/dev/snd/pcmC2D0c") else [])
    node = "/dev/snd/pcmC{n}D0c"
    assert ss.list_cards(node) == ["ATR4697USB", "Webcam"]
    assert ss.pick_card("Webcam", None, node) == (2, "Webcam")
    assert ss.pick_card("Nope", None, node) == (1, "ATR4697USB")      # unknown name: the default (first USB)
    assert ss.pick_card(None, None, node) == (1, "ATR4697USB")
    subs = ss.Subscriptions()
    subs.on_packet(vban.subscribe_packet(["room-mic"], 0, devices={"room-mic": "Webcam"}), ("10.0.0.5", 6980), 1.0)
    assert subs.device("room-mic") == "Webcam" and subs.device("room-piano") is None
    sink = Sink()
    au = ss.AudioStream("room-mic", sink, dev="Webcam")
    au.devices = ss.list_cards(node)
    au.heartbeat("ok", 10.0)
    hb = vban.parse_heartbeat(sink.parsed()[-1])
    assert hb.devices == ["ATR4697USB", "Webcam"] and hb.dev == "Webcam"


def test_test_piano_streams_to_the_subscriber_over_udp():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(0.2)
    out = ss.Out(0, "127.0.0.1")
    port = out.sock.getsockname()[1]
    stop = threading.Event()
    threading.Thread(target=out.serve, args=(stop,), daemon=True).start()
    rx.sendto(vban.subscribe_packet(["test-piano"], 0), ("127.0.0.1", port))
    t_end = time.monotonic() + 2.0
    while out.subs.target("test-piano", time.monotonic()) is None and time.monotonic() < t_end:
        time.sleep(0.005)                                 # subscribed before the first note, so seq 1 isn't lost
    threading.Thread(target=ss.run_test_piano, args=(out, stop, "scale"), daemon=True).start()
    got, t_end, counter = [], time.monotonic() + 3.0, 1
    try:
        while time.monotonic() < t_end:
            rx.sendto(vban.subscribe_packet(["test-piano"], counter), ("127.0.0.1", port))
            counter += 1
            try:
                while True:
                    buf, _ = rx.recvfrom(2048)
                    got.append(vban.parse(buf))
            except socket.timeout:
                pass
    finally:
        stop.set()
    kinds = [vban.classify(p) for p in got]
    assert "midi_events" in kinds and "midi_state" in kinds and all(p.stream == "test-piano" for p in got)
    seqs = [vban.parse_midi_events(p).seq for p in got if vban.classify(p) == "midi_events"]
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) >= 4


def test_mic_captures_only_while_subscribed(monkeypatch):
    """Off in the SUBSCRIBE: no arecord, off heartbeats with the devices; on: arecord opens; off again: it closes."""
    opened, closed = [], []

    class FakeProc:
        def __init__(self, cmd, **kw):
            opened.append(cmd)
            self.stdout = self
            self.stderr = io.BytesIO(b"")

        def read(self, n):
            time.sleep(0.002)
            return b"\0" * n

        def terminate(self):
            closed.append(1)

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        def poll(self):
            return 0

    monkeypatch.setattr(ss.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(ss, "pick_card", lambda hint, match, node: (1, "ATR4697USB"))
    monkeypatch.setattr(ss, "list_cards", lambda node: ["ATR4697USB"])
    monkeypatch.setattr(ss, "_have", lambda cmd: True)
    out = ss.Out(0, "127.0.0.1")
    sent = []
    monkeypatch.setattr(out, "send", lambda stream, pkt: (sent.append(vban.parse(pkt)), True)[1])
    stop = threading.Event()
    th = threading.Thread(target=ss.run_arecord, args=(out, stop, None), daemon=True)
    addr = ("127.0.0.1", 9)
    try:
        out.subs.on_packet(vban.subscribe_packet(["room-piano"], 0, off=["room-mic"]), addr, time.monotonic())
        th.start()
        time.sleep(0.8)
        assert not opened
        hbs = [vban.parse_heartbeat(p) for p in sent if vban.classify(p) == "heartbeat"]
        assert hbs and all(h.state == "off" and h.frame is None and h.devices == ["ATR4697USB"] for h in hbs)
        out.subs.on_packet(vban.subscribe_packet(["room-piano", "room-mic"], 1), addr, time.monotonic())
        time.sleep(0.5)
        assert len(opened) == 1 and not closed
        out.subs.on_packet(vban.subscribe_packet(["room-piano"], 2, off=["room-mic"]), addr, time.monotonic())
        time.sleep(0.5)
        assert closed and len(opened) == 1
    finally:
        stop.set()
        th.join(timeout=2)


# -- the test piano's rendered sound (piano_render/session.py's protocol; a fake helper, no audio) ----------------
import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import statistics  # noqa: E402
import subprocess  # noqa: E402
import types  # noqa: E402

HERE = __import__("pathlib").Path(__file__).resolve().parent
REPO = HERE.parents[1]
EVENTS = [(0.0, 0x90, 60, 80), (0.1, 0x80, 60, 40), (0.2, 0x90, 64, 80), (0.3, 0x80, 64, 40),
          (0.4, 0x90, 67, 80), (0.6, 0x80, 67, 40)]
FAKE_HELPER = r'''
import json, os, select, sys, time
args = sys.argv[1:]
def log(o):
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps(o) + "\n")
def emit(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()
log({"argv": args})
mode = os.environ.get("FAKE_MODE", "ok")
dur = float(os.environ.get("FAKE_DUR", "1.0"))
start_in = float(os.environ.get("FAKE_START_IN", "0.15"))
pad = float(args[args.index("--pad-s") + 1])
emit({"ev": "progress", "stage": "rendering", "step": "engine", "done_s": 0.5, "total_s": 1.0, "frac": 0.4})
time.sleep(0.15)
if mode == "error":
    emit({"ev": "error", "why": "the instrument's samples are missing"})
    sys.exit(1)
if mode == "die":
    sys.exit(3)
if os.environ.get("FAKE_STREAMING"):                  # today's helper: it renders as it plays
    emit({"ev": "rendered", "streaming": True, "path": None, "duration_s": dur, "onset_ms": 2.0, "cached": False,
          "render_s": 0.15, "load_s": 0.15})
else:                                                 # an older helper: a rendered file
    emit({"ev": "rendered", "path": os.environ.get("FAKE_PATH", "/nowhere.flac"), "duration_s": dur, "onset_ms": 2.0,
          "cached": False, "render_s": 0.15})
buf, until = b"", None
while True:
    r, _, _ = select.select([0], [], [], 0.005)
    if until is not None and time.monotonic() >= until:
        until = None
        emit({"ev": "played", "frames": 1, "underruns": 0})
    if not r:
        continue
    chunk = os.read(0, 4096)
    if not chunk:
        log({"cmd": "EOF", "t": time.monotonic()})
        break
    buf += chunk
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        cmd = line.decode().strip()
        log({"cmd": cmd, "t": time.monotonic()})
        if cmd == "play" or cmd.startswith("play-sonos "):
            if cmd != "play":
                log({"sonos": json.loads(cmd[len("play-sonos "):])})
                if os.environ.get("FAKE_WARN"):
                    emit({"ev": "warning", "why": os.environ["FAKE_WARN"]})
            t_audio0 = time.monotonic() + start_in + pad
            log({"t_audio0": t_audio0})
            emit({"ev": "started", "t_audio0": t_audio0, "pad_s": pad, "startup_s": start_in})
            if os.environ.get("FAKE_REBASE_S"):
                t_audio0 += float(os.environ["FAKE_REBASE_S"])
                log({"rebased": t_audio0})
                emit({"ev": "rebase", "t_audio0": t_audio0, "was": t_audio0 - float(os.environ["FAKE_REBASE_S"])})
            until = t_audio0 + dur
        elif cmd == "stop":
            until = None
            emit({"ev": "stopped"})
'''


class RecOut:
    """An Out stand-in: every packet with the monotonic ns it was sent at."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, bytes]] = []
        self.lock = threading.Lock()

    def send(self, stream: str, packet: bytes) -> bool:
        with self.lock:
            self.sent.append((time.monotonic_ns(), packet))
        return True

    def events(self) -> list[tuple]:
        """[(t_sent_ns, stamp_ns, status, d1, d2)] of every MIDI event sent."""
        with self.lock:
            sent = list(self.sent)
        out = []
        for t, pk in sent:
            p = vban.parse(pk)
            if vban.classify(p) == "midi_events":
                out += [(t, *e) for e in vban.parse_midi_events(p).ev]
        return out


@pytest.fixture
def tiny(tmp_path):
    p = tmp_path / "tiny.mid"
    p.write_bytes(ss.smf_bytes(EVENTS))
    return p


@pytest.fixture
def helper(tmp_path, monkeypatch):
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE_HELPER)
    logp = tmp_path / "helper.log"
    monkeypatch.setenv(ss.SESSION_CMD_ENV, f"{sys.executable} {script}")
    monkeypatch.setenv("FAKE_LOG", str(logp))

    def lines() -> list[dict]:
        return [json.loads(x) for x in logp.read_text().splitlines()] if logp.exists() else []
    return types.SimpleNamespace(log=lines, cmds=lambda: [e["cmd"] for e in lines() if "cmd" in e],
                                 t_audio0=lambda: [e["t_audio0"] for e in lines() if "t_audio0" in e])


def record_stages(monkeypatch) -> list:
    stages = []
    real = ss.TestStatus.set

    def rec(self, stage, progress=None, error=None):
        stages.append((stage, progress, error))
        real(self, stage, progress, error)
    monkeypatch.setattr(ss.TestStatus, "set", rec)
    return stages


def check_timing(ev, events, t_audio0: float, lead_s: float) -> list[float]:
    """Each event stamped at t_audio0 + t_s - lead (1 ms) and sent within a few ms of it; the send errors in ms."""
    assert [(st, d1, d2) for *_, st, d1, d2 in ev] == [(st, d1, d2) for _, st, d1, d2 in events]
    errs = []
    for (t_sent, stamp, *_), (t_s, *_) in zip(ev, events):
        ideal = (t_audio0 + t_s - lead_s) * 1e9
        assert abs(stamp - ideal) < 1e6
        errs.append((t_sent - ideal) / 1e6)
    assert min(errs) > -1.0 and max(errs) < 10.0 and statistics.median(errs) < 3.0, errs
    return errs


def test_smf_round_trip_and_pad():
    from stream_service.piano_replay import read_midi
    back = read_midi(ss.smf_bytes(EVENTS + [(1.23456, 0xB0, 64, 127)]))
    assert [(st, a, b) for _, st, a, b in back] == [(st, a, b) for _, st, a, b in EVENTS] + [(0xB0, 64, 127)]
    assert all(abs(x[0] - y[0]) < 1e-4 for x, y in zip(back, EVENTS + [(1.23456, 0, 0, 0)]))
    assert ss.pad_for(EVENTS, 0.65) == pytest.approx(0.75)                 # first note at 0: lead + 0.1
    assert ss.pad_for([(2.0, 0x90, 60, 1)], 0.65) == 0.0                     # a late first note needs none
    assert ss.pad_for([(0.5, 0x90, 60, 1)], 0.65) == pytest.approx(0.25)
    assert ss.pad_for(EVENTS, 0.0) == pytest.approx(0.1)
    assert ss.is_rendered("concert-d") and not ss.is_rendered("windows-gs") and not ss.is_rendered(None)


def test_sources_sees_the_flags_and_its_argv_parses(tmp_path):
    """show_service's own argv for a rendered run against this very script: every flag recognised and parsed."""
    from show_service import contract, sources
    from show_service.contract import BrainContext
    script = HERE / "show_sender.py"
    assert sources.sender_flags(script) == frozenset(sources.SENDER_FLAGS)
    (tmp_path / "piano").mkdir()
    (tmp_path / "piano" / "Nocturne.mid").write_bytes(ss.smf_bytes(EVENTS))
    (tmp_path / "grand.sf2").write_bytes(b"RIFF")
    (tmp_path / "instruments.json").write_text(json.dumps({"root": str(tmp_path), "instruments": [
        {"id": "grand", "label": "Grand", "engine": "fluidsynth", "file": "grand.sf2", "order": 1},
        {"id": "windows-gs", "label": "Basic", "engine": "windows-gs", "order": 2}]}))
    argvs = []

    class P:
        pid = 1

        def __init__(self, argv, **kw):
            argvs.append(list(argv))
            self.rc = None

        def poll(self):
            return self.rc

        def terminate(self):
            self.rc = -15

        def kill(self):
            self.rc = -9

        def wait(self, timeout=None):
            return self.rc
    ctx = BrainContext(publish=lambda m: None, set_senders=lambda s: None, set_priority=lambda p: None)
    src = sources.Sources(ctx, python="py", piano_script=script, log_dir=tmp_path / "state", popen=P, pdeath=False,
                          instruments_file=tmp_path / "instruments.json", recordings_dir=tmp_path / "recs",
                          set_offset=lambda s, ms: None)
    src.configure({"tests": {"piano": {"pieces": str(tmp_path / "piano")}}})
    src.start()
    assert [r["id"] for r in src.instruments()[0]] == ["grand", "windows-gs"]     # rendered sounds offered
    src.start_test("piano", "file:Nocturne.mid", instrument="grand", once=True)
    a = ss.build_parser().parse_args(argvs[-1][2:])
    assert (a.instrument, a.output, a.lead_ms, a.once) == ("grand", "pc22", 650.0, True)
    assert a.status_file == str(tmp_path / "state" / "test-piano.status.json") and a.test_piano.endswith(".mid")
    src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs", output="none")
    src.tick(0.0)                                                          # the old child has let go: spawned
    a = ss.build_parser().parse_args(argvs[-1][2:])
    assert a.no_speaker and a.instrument is None and not a.once
    src.stop()
    assert contract.TEST_PIANO_SCRIPT.resolve() == script


def test_rendered_timing_pad_status_and_once(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setenv("FAKE_DUR", "0.8")
    stages = record_stages(monkeypatch)
    status = tmp_path / "status.json"
    out, stop = RecOut(), threading.Event()
    rc = ss.run_test_piano(out, stop, str(tiny), True, instrument="concert-d", output="pc22",
                           status_file=str(status), lead_ms=300, once=True)
    t_end = time.monotonic()
    assert rc == 0
    argv = helper.log()[0]["argv"]
    assert argv[argv.index("--midi") + 1] == str(tiny.resolve()) and argv[argv.index("--instrument") + 1] == "concert-d"
    assert argv[argv.index("--pad-s") + 1] == "0.400"                       # 0.3 lead - 0.0 first note + 0.1
    assert helper.cmds() == ["play", "stop", "EOF"]
    (t_audio0,) = helper.t_audio0()
    errs = check_timing(out.events(), EVENTS, t_audio0, 0.3)
    print(f"send error ms: median {statistics.median(errs):.2f} max {max(errs):.2f}")
    assert t_end >= t_audio0 + 0.8                                         # after the audio, not just the MIDI
    names = [s[0] for s in stages]
    assert names[0] == "preparing" and ("preparing", 0.4, None) in stages and "playing" in names
    assert stages[-1] == ("done", 1.0, None)
    assert json.loads(status.read_text()) == {"stage": "done", "progress": 1.0, "error": None}


def test_rendered_loops_with_a_new_t_audio0_each_pass(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setenv("FAKE_DUR", "0.7")
    monkeypatch.setattr(ss, "LOOP_GAP_S", 0.1)
    status = tmp_path / "status.json"
    out, stop, rc = RecOut(), threading.Event(), []
    th = threading.Thread(target=lambda: rc.append(ss.run_test_piano(
        out, stop, str(tiny), True, instrument="concert-d", output="pc22", status_file=str(status), lead_ms=200)))
    th.start()
    t_end = time.monotonic() + 8.0
    while time.monotonic() < t_end and len(out.events()) < 2 * len(EVENTS):
        time.sleep(0.02)
    stop.set()
    th.join(5.0)
    assert rc == [0] and not th.is_alive()
    a1, a2 = helper.t_audio0()[:2]
    assert a2 >= a1 + 0.7                                                  # the next play after the audio ended
    ev = out.events()
    check_timing(ev[:len(EVENTS)], EVENTS, a1, 0.2)
    check_timing(ev[len(EVENTS):2 * len(EVENTS)], EVENTS, a2, 0.2)
    assert helper.cmds()[:2] == ["play", "play"] and helper.cmds()[-2:] == ["stop", "EOF"]
    assert json.loads(status.read_text())["stage"] == "done"


@pytest.mark.parametrize("mode, why", [("error", "the instrument's samples are missing"), ("die", "exit code 3")])
def test_a_helper_failure_is_a_status_error_and_exit_1(tiny, tmp_path, helper, monkeypatch, mode, why):
    monkeypatch.setenv("FAKE_MODE", mode)
    status = tmp_path / "status.json"
    rc = ss.run_test_piano(RecOut(), threading.Event(), str(tiny), True, instrument="concert-d", output="pc22",
                           status_file=str(status), lead_ms=300, once=True)
    doc = json.loads(status.read_text())
    assert rc == 1 and doc["stage"] == "error" and why in doc["error"]


def test_no_helper_installed_is_a_status_error(tiny, tmp_path, monkeypatch):
    monkeypatch.setenv(ss.SESSION_CMD_ENV, str(tmp_path / "no-such-python"))
    status = tmp_path / "status.json"
    rc = ss.run_test_piano(RecOut(), threading.Event(), str(tiny), True, instrument="concert-d", output="pc22",
                           status_file=str(status), once=True)
    doc = json.loads(status.read_text())
    assert rc == 1 and doc["stage"] == "error" and "isn't installed" in doc["error"]


def test_rendered_screens_only_is_midi_only_without_lead(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setattr(ss, "ONCE_TAIL_S", 0.1)
    status = tmp_path / "status.json"
    out = RecOut()
    t = time.monotonic()
    rc = ss.run_test_piano(out, threading.Event(), str(tiny), True, instrument="concert-d", output="none",
                           status_file=str(status), lead_ms=300, once=True)
    assert rc == 0 and helper.log() == []                                  # no helper, no render, no sound
    ev = out.events()
    t0 = ev[0][1] / 1e9
    assert 0 <= t0 - t < 0.05                                              # no lead: from now
    check_timing(ev, EVENTS, t0, 0.0)
    assert json.loads(status.read_text()) == {"stage": "done", "progress": 1.0, "error": None}


def test_windows_synth_once_without_speaker_ends(tiny, monkeypatch):
    monkeypatch.setattr(ss, "ONCE_TAIL_S", 0.1)
    out = RecOut()
    t = time.monotonic()
    assert ss.run_test_piano(out, threading.Event(), str(tiny), False, once=True) == 0
    assert 0.6 <= time.monotonic() - t < 1.5 and len(out.events()) == len(EVENTS)


def _sender(tiny, status, *extra):
    return subprocess.Popen([sys.executable, str(HERE / "show_sender.py"), "--test-piano", str(tiny), "--port", "0",
                             "--bind", "127.0.0.1", "--instrument", "concert-d", "--output", "pc22",
                             "--status-file", str(status), "--lead-ms", "300", *extra], cwd=str(REPO),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def test_sigterm_stops_the_helper_and_exits_0(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setenv("FAKE_DUR", "30")
    status = tmp_path / "status.json"
    p = _sender(tiny, status)
    try:
        t_end = time.monotonic() + 10.0
        while time.monotonic() < t_end:
            try:
                if json.loads(status.read_text())["stage"] == "playing":
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
        else:
            raise AssertionError("never playing: " + p.stderr.read1(4096).decode())
        t = time.monotonic()
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0
        assert time.monotonic() - t < 1.5
    finally:
        if p.poll() is None:
            p.kill()
    assert helper.cmds()[-2:] == ["stop", "EOF"]
    assert json.loads(status.read_text())["stage"] == "done"


def test_main_exits_1_on_a_helper_error(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    status = tmp_path / "status.json"
    p = _sender(tiny, status, "--once")
    assert p.wait(timeout=10) == 1
    assert json.loads(status.read_text())["error"] == "the instrument's samples are missing"


# -- Play on Sonos ------------------------------------------------------------------------------------------------------
KEY = "0123456789abcdef0123456789abcdef"


def test_the_sonos_url_comes_from_sm_lan_host_in_env_and_the_rendered_key(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nSM_LAN_HOST='10.9.8.7'\n")
    monkeypatch.setattr(ss, "ENV_FILE", env)
    monkeypatch.setenv("SM_LAN_HOST", "not-this-one")
    assert ss.sonos_url(f"/c/renders/{KEY}.flac") == f"http://10.9.8.7:8700/show/api/v1/piano/audio/{KEY}.flac"
    assert ss.sonos_url(None) == "http://10.9.8.7:8700/show"                # a stream: the door the Sonos reads it from
    env.write_text("OTHER=1\n")
    assert ss.lan_host() == "not-this-one"                                 # no .env line: the environment
    monkeypatch.delenv("SM_LAN_HOST")
    with pytest.raises(ss.HelperFailed, match="SM_LAN_HOST"):
        ss.sonos_url(f"/c/{KEY}.flac")
    with pytest.raises(ss.HelperFailed, match="serves"):
        ss.sonos_url("/c/../etc.flac", host="h")
    assert ss.piece_label("file:Jazz - Bill_Evans.mid") == "Jazz - Bill Evans"
    assert ss.piece_label("/recs/20261022-140512-ab12.mid") == "20261022-140512-ab12"
    a = ss.build_parser().parse_args(["--test-piano", "x", "--output", "sonos:drawing-room", "--title", "Nocturne"])
    assert (a.output, a.title) == ("sonos:drawing-room", "Nocturne")
    with pytest.raises(SystemExit):
        ss.build_parser().parse_args(["--test-piano", "x", "--output", "sonos:a/b"])


def test_rendered_on_sonos_play_sonos_url_title_pad_rebase_warning_and_once(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setattr(ss, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text("SM_LAN_HOST=192.0.2.5\n")
    monkeypatch.setenv("FAKE_PATH", f"/cache/{KEY}.flac")
    monkeypatch.setenv("FAKE_DUR", "0.8")
    monkeypatch.setenv("FAKE_REBASE_S", "0.25")
    monkeypatch.setenv("FAKE_WARN", "Sonos volume is 0")
    stages = record_stages(monkeypatch)
    status = tmp_path / "status.json"
    out, stop = RecOut(), threading.Event()
    rc = ss.run_test_piano(out, stop, str(tiny), True, instrument="concert-d", output="sonos:drawing-room",
                           status_file=str(status), lead_ms=300, once=True, title="Tiny tune")
    assert rc == 0
    argv = helper.log()[0]["argv"]
    assert argv[argv.index("--output") + 1] == "sonos:drawing-room"
    assert argv[argv.index("--pad-s") + 1] == "0.400"                       # 0.3 - 0 + 0.1 (the helper takes off
                                                                            # what the Sonos's own start gives)
    (cmd,) = [e["sonos"] for e in helper.log() if "sonos" in e]
    assert cmd == {"url": f"http://192.0.2.5:8700/show/api/v1/piano/audio/{KEY}.flac", "title": "Piano: Tiny tune"}
    assert helper.cmds()[0].startswith("play-sonos ") and helper.cmds()[-2:] == ["stop", "EOF"]
    (rebased,) = [e["rebased"] for e in helper.log() if "rebased" in e]
    check_timing(out.events(), EVENTS, rebased, 0.3)                       # the MIDI follows the rebased clock
    assert ("playing", 0.0, "Sonos volume is 0") in stages                 # the warning, shown while it plays
    assert stages[-1] == ("done", 1.0, None)


def test_a_streaming_helper_on_sonos_gets_the_door_and_the_midi_follows_its_t_audio0(tiny, tmp_path, helper,
                                                                                      monkeypatch):
    monkeypatch.setattr(ss, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text("SM_LAN_HOST=192.0.2.5\n")
    monkeypatch.setenv("FAKE_STREAMING", "1")
    monkeypatch.setenv("FAKE_DUR", "0.8")
    monkeypatch.setenv("FAKE_START_IN", "1.6")                              # the Sonos is heard 1.6 s after "play"
    stages = record_stages(monkeypatch)
    out, stop = RecOut(), threading.Event()
    rc = ss.run_test_piano(out, stop, str(tiny), True, instrument="concert-d", output="sonos:drawing-room",
                           status_file=str(tmp_path / "status.json"), lead_ms=650, once=True, title="Tiny tune")
    assert rc == 0
    (cmd,) = [e["sonos"] for e in helper.log() if "sonos" in e]
    assert cmd == {"url": "http://192.0.2.5:8700/show", "title": "Piano: Tiny tune"}
    (t_audio0,) = helper.t_audio0()
    errs = check_timing(out.events(), EVENTS, t_audio0, 0.65)               # later than the lead: later MIDI, on time
    assert max(errs) < 10.0 and stages[0] == ("preparing", 0.0, None) and stages[-1] == ("done", 1.0, None)


def test_a_memory_abort_is_said_plainly():
    assert "ran out of memory" in ss._why({"ev": "_eof", "rc": -6})
    assert "exit code 3" in ss._why({"ev": "_eof", "rc": 3})


def test_sonos_loops_by_playing_again_and_needs_a_lan_host(tiny, tmp_path, helper, monkeypatch):
    monkeypatch.setattr(ss, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text("SM_LAN_HOST=192.0.2.5\n")
    monkeypatch.setenv("FAKE_PATH", f"/cache/{KEY}.flac")
    monkeypatch.setenv("FAKE_DUR", "0.7")
    monkeypatch.setattr(ss, "LOOP_GAP_S", 0.1)
    out, stop, rc = RecOut(), threading.Event(), []
    th = threading.Thread(target=lambda: rc.append(ss.run_test_piano(
        out, stop, str(tiny), True, instrument="concert-d", output="sonos:drawing-room",
        status_file=str(tmp_path / "s.json"), lead_ms=200)))
    th.start()
    t_end = time.monotonic() + 10.0
    while time.monotonic() < t_end and len(out.events()) < 2 * len(EVENTS):
        time.sleep(0.02)
    stop.set()
    th.join(5.0)
    assert rc == [0] and [c for c in helper.cmds() if c.startswith("play")][:2] == [helper.cmds()[0]] * 2
    assert helper.cmds()[0].startswith("play-sonos ")
    (tmp_path / ".env").write_text("")
    monkeypatch.delenv("SM_LAN_HOST", raising=False)
    status = tmp_path / "status.json"
    rc = ss.run_test_piano(RecOut(), threading.Event(), str(tiny), True, instrument="concert-d",
                           output="sonos:drawing-room", status_file=str(status), lead_ms=300, once=True)
    doc = json.loads(status.read_text())
    assert rc == 1 and doc["stage"] == "error" and "SM_LAN_HOST" in doc["error"]


def test_module_load_imports_nothing_of_the_test_piano():
    """The kiosk runs this file for room-mic/room-piano: loading it starts and imports none of the test piano."""
    code = ("import sys, threading; sys.path.insert(0, sys.argv[1]); import show_sender; "
            "print(sorted(m for m in ('piano_render', 'stream_service.piano_replay', 'queue', 'shlex', 'hashlib') "
            "if m in sys.modules), threading.active_count())")
    out = subprocess.run([sys.executable, "-I", "-c", code, str(HERE)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    loaded, threads = out.stdout.rsplit(" ", 1)
    assert "piano_render" not in loaded and "piano_replay" not in loaded and threads.strip() == "1"
