"""Test sources and the sender list (show_service/sources.py), the test-audio child (show_service/test_audio.py) and
tools/show/fetch_test_music.py (README "Stage 3", S3.10).

Everything runs on ephemeral 127.0.0.1 ports and tmp folders: never 6980-6982, never a real device, never the real
state or test-music folders, never the network (the fetch tests use file:// URLs). Children are only the stand-in
scripts and test_audio processes these tests start, and every one is stopped here.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import numpy as np
import pytest
import yaml

from show_service import contract, sources, test_audio, vban
from show_service.contract import AUDIO_SEGMENT, BrainContext, Config, Sender
from show_service.receiver import Receiver

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")

FAKE_CHILD = r'''
import os, signal, sys, time
print("fake child", " ".join(sys.argv[1:]), flush=True)
mode = os.environ.get("FAKE_MODE", "run")
if mode == "fail":
    print("loading piece", flush=True)
    print("boom: no such piece", flush=True)
    sys.exit(3)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(0.05)
'''


# -- helpers ----------------------------------------------------------------------------------------------------------
class Rec:
    def __init__(self) -> None:
        self.senders: list[tuple] = []
        self.priority: list[tuple] = []

    def set_senders(self, s) -> None:
        self.senders.append(tuple(s))

    def set_priority(self, p) -> None:
        self.priority.append(tuple(p))


class Health:
    """A fake Receiver.health(): the test sets stream states."""

    def __init__(self) -> None:
        self.streams: dict[str, dict] = {}
        self.t_ms: float | None = None

    def __call__(self) -> dict:
        h = {"streams": self.streams}
        if self.t_ms is not None:
            h["t_ms"] = self.t_ms
        return h


def settings_for(tmp: pathlib.Path, *, audio_port: int = 40001, piano_port: int = 40002,
                 rank_first: bool = True) -> dict:
    return {
        "senders": {
            "kiosk": {"hosts": ["192.0.2.10", "192.0.2.11"], "port": 6980, "streams": ["room-mic", "room-piano"]},
            "test-piano": {"hosts": ["localhost"], "port": piano_port, "streams": ["test-piano"], "on_demand": True},
            "test-audio": {"hosts": ["localhost"], "port": audio_port, "streams": ["test-audio"], "on_demand": True},
        },
        "tests": {"piano": {"sender": "test-piano", "pieces": str(tmp / "piano")},
                  "audio": {"sender": "test-audio", "music": str(tmp / "music"), "rank_first": rank_first}},
    }


@pytest.fixture
def lib(tmp_path):
    """tmp piano and music folders with a couple of pieces."""
    (tmp_path / "piano").mkdir()
    (tmp_path / "piano" / "Jazz_-_Some_Tune.mid").write_bytes(b"MThd")
    (tmp_path / "piano" / "Bach prelude.MIDI").write_bytes(b"MThd")
    (tmp_path / "piano" / "notes.txt").write_text("not a piece")
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "jazz-x.mp3").write_bytes(b"ID3")
    (tmp_path / "music" / "house_y.ogg").write_bytes(b"OggS")
    (tmp_path / "music" / "index.json").write_text(json.dumps(
        {"jazz-x": {"title": "Tune X", "artist": "Someone", "genre": "jazz", "license": "CC BY", "file": "jazz-x.mp3",
                    "duration_s": 61.5}}))
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD)
    return tmp_path


def make_sources(lib, *, health=None, static=(), clock=time.monotonic, settings=None, **ctx_kw):
    rec = Rec()
    ctx = BrainContext(publish=lambda m: None, set_senders=rec.set_senders, set_priority=rec.set_priority,
                       static_senders=tuple(static), health=health, **ctx_kw)
    script = lib / "fake_child.py"
    src = sources.Sources(ctx, piano_script=script, audio_cmd=[sys.executable, str(script)], log_dir=lib / "state",
                          clock=clock)
    src.configure(settings if settings is not None else settings_for(lib))
    src.start()
    return src, rec


def wait_for(src, pred, timeout=5.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        src.tick(0.0)
        if pred():
            return True
        time.sleep(step)
    return pred()


def names(senders) -> list[str]:
    return [s.name for s in senders]


# -- senders (pure) ---------------------------------------------------------------------------------------------------
def test_parse_senders_reads_hosts_and_skips_bad_entries():
    got, errors = sources.parse_senders({"senders": {
        "kiosk": {"hosts": ["192.0.2.10", "192.0.2.11"], "port": 6980, "streams": ["room-mic", "room-piano"]},
        "one": {"host": "box.lan", "streams": "room-line"},                    # single host/stream, default port
        "noport": {"hosts": ["a"], "port": 70000, "streams": ["x"]},
        "nohosts": {"hosts": [], "streams": ["x"]},
        "nostreams": {"hosts": ["a"], "port": 1},
        "bad od": {"hosts": ["a"], "streams": ["x"]},
        "od": {"hosts": ["a"], "streams": ["x"], "on_demand": "yes"},
    }})
    assert [s.name for s in got] == ["kiosk", "one"]
    k = got[0]
    assert k.host == "192.0.2.10" and k.hosts == ("192.0.2.10", "192.0.2.11") and k.streams == ("room-mic", "room-piano")
    assert got[1] == Sender("one", "box.lan", 6980, ("room-line",), hosts=("box.lan",), on_demand=False)
    assert len(errors) == 5 and all("skipped" in e for e in errors)
    assert sources.parse_senders({}) == ([], [])
    assert sources.parse_senders({"senders": ["x"]})[1]


def test_senders_from_settings_static_yaml_and_on_demand():
    settings = {"senders": {
        "kiosk": {"hosts": ["192.0.2.10"], "port": 6980, "streams": ["room-mic"]},
        "test-audio": {"hosts": ["localhost"], "port": 40001, "streams": ["test-audio"], "on_demand": True}}}
    static = (Sender("kiosk", "old-name", 6980, ("room-mic",)), Sender("pc22", "pc22.lan", 6980, ("pc22-loop",)))
    eff = sources.senders_from_settings(settings, static, running=())
    assert names(eff) == ["pc22", "kiosk"]                   # show.yaml's kiosk replaces the static one
    assert eff[1].hosts == ("192.0.2.10",)
    eff = sources.senders_from_settings(settings, static, running={"test-audio"})
    assert names(eff) == ["pc22", "kiosk", "test-audio"] and eff[2].on_demand
    assert sources.senders_from_settings({}, static, ()) == static


def test_the_shipped_show_yaml_gives_valid_senders():
    settings = yaml.safe_load(contract.SHOW_FILE.read_text(encoding="utf-8"))
    got, errors = sources.parse_senders(settings)
    assert not errors and got
    by = {s.name: s for s in got}
    for name in (settings["tests"]["piano"]["sender"], settings["tests"]["audio"]["sender"]):
        assert by[name].on_demand
    always = names(sources.senders_from_settings(settings, (), ()))
    assert not any(by[n].on_demand for n in always)
    # the always-on senders are the room's own (pc22 since 16 Oct; the kiosk is the owner's go-live step, README
    # S3.14), never the test senders; test audio carries no room sound
    assert not set(always) & {settings["tests"]["piano"]["sender"], settings["tests"]["audio"]["sender"]}
    assert settings["senders"]["test-audio"]["room_sound"] is False


# -- pieces -----------------------------------------------------------------------------------------------------------
def test_pieces_lists_files_phrases_and_index_labels(lib):
    src, _ = make_sources(lib)
    p = src.pieces()
    piano_files = [x for x in p["piano"] if x["kind"] == "file"]
    assert [x["id"] for x in piano_files] == ["file:Bach prelude.MIDI", "file:Jazz_-_Some_Tune.mid"]
    assert piano_files[1]["label"] == "Jazz - Some Tune"
    assert [x for x in p["piano"] if x["kind"] == "phrase"]          # stream_service/piano_replay.py's phrases
    a = {x["id"]: x for x in p["audio"]}
    assert set(a) == {"file:jazz-x.mp3", "file:house_y.ogg"}
    assert a["file:jazz-x.mp3"] == {"id": "file:jazz-x.mp3", "label": "Tune X", "kind": "file", "genre": "jazz",
                                    "artist": "Someone", "license": "CC BY", "duration_s": 61.5}
    assert a["file:house_y.ogg"] == {"id": "file:house_y.ogg", "label": "House y", "kind": "file"}


def test_unknown_source_and_piece_are_value_errors(lib):
    src, _ = make_sources(lib)
    with pytest.raises(ValueError, match="unknown_source"):
        src.start_test("organ")
    with pytest.raises(ValueError, match="unknown_piece"):
        src.start_test("piano", "file:nope.mid")
    empty = settings_for(lib)
    empty["tests"]["audio"]["music"] = str(lib / "missing")
    src.configure(empty)
    with pytest.raises(ValueError, match="unknown_piece"):
        src.start_test("audio")
    assert any("tests.audio.music" in e for e in src.snapshot()["errors"])
    assert src.status()["audio"]["state"] == "off"


# -- senders hot-reload -------------------------------------------------------------------------------------------------
def test_configure_hands_the_receiver_the_sender_list_once(lib):
    static = (Sender("pc22", "pc22.lan", 6980, ("pc22-loop",)),)
    src, rec = make_sources(lib, static=static)
    assert len(rec.senders) == 1
    assert names(rec.senders[0]) == ["pc22", "kiosk"]                 # on_demand test senders not yet
    assert rec.senders[0][1].hosts == ("192.0.2.10", "192.0.2.11") and rec.senders[0][1].host == "192.0.2.10"
    src.configure(settings_for(lib))                                  # unchanged: no call
    src.tick(0.0)
    assert len(rec.senders) == 1
    changed = settings_for(lib)
    changed["senders"]["kiosk"]["hosts"] = ["192.0.2.12"]
    src.configure(changed)                                            # a show.yaml edit
    assert len(rec.senders) == 2 and rec.senders[1][1].hosts == ("192.0.2.12",)
    snap = src.snapshot()
    rows = {r["name"]: r for r in snap["senders"]}
    assert rows["kiosk"]["subscribed"] and not rows["test-audio"]["subscribed"] and rows["test-audio"]["on_demand"]
    assert snap["priority"] == [] and set(snap["tests"]) == {"piano", "audio"}


def test_missing_receiver_seam_degrades_with_an_error(lib):
    ctx = BrainContext(publish=lambda m: None)
    src = sources.Sources(ctx, log_dir=lib / "state")
    src.configure(settings_for(lib))
    src.start()
    assert any("set_senders" in e for e in src.snapshot()["errors"])
    assert not any(r["subscribed"] for r in src.snapshot()["senders"])


# -- children (stand-in script) -------------------------------------------------------------------------------------------
def test_piano_child_starts_plays_and_stops(lib):
    health = Health()
    src, rec = make_sources(lib, health=health)
    st = src.start_test("piano", "file:Jazz_-_Some_Tune.mid")
    try:
        assert st["state"] == "starting" and st["stream"] == "test-piano" and st["sender"] == "test-piano"
        assert st["pid"] and st["label"] == "Jazz - Some Tune"
        assert names(rec.senders[-1]) == ["kiosk", "test-piano"]      # subscribed while it runs
        log_path = lib / "state" / "test-piano.log"
        assert wait_for(src, lambda: "fake child" in log_path.read_text())
        text = log_path.read_text()
        assert "--test-piano " + str(lib / "piano" / "Jazz_-_Some_Tune.mid") in text   # not the default library
        assert "--port 40002 --bind 127.0.0.1" in text
        src.tick(0.0)
        assert src.status()["piano"]["state"] == "starting"           # nothing streaming yet
        health.streams["test-piano"] = {"state": "streaming", "net": {"age_ms": 20.0}}
        assert wait_for(src, lambda: src.status()["piano"]["state"] == "playing")
        assert src.snapshot()["tests"]["piano"]["state"] == "playing"
        assert src.stop_test("piano") == ["piano"]
        assert src.status()["piano"]["state"] == "stopping"
        assert wait_for(src, lambda: src.status()["piano"]["state"] == "off")
        assert names(rec.senders[-1]) == ["kiosk"]
        assert src.stop_test("piano") == []
    finally:
        src.stop()


def test_a_child_that_exits_is_failed_with_its_last_lines(lib, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    src, rec = make_sources(lib, health=Health())
    try:
        src.start_test("piano", "chords")                              # a built-in phrase id
        assert wait_for(src, lambda: src.status()["piano"]["state"] == "failed")
        st = src.status()["piano"]
        assert "boom: no such piece" in st["error"] and "loading piece" in st["error"]
        assert "show_service starts" not in st["error"]
        assert names(rec.senders[-1]) == ["kiosk"]                    # unsubscribed again
        assert src.stop_test() == [] and src.status()["piano"]["state"] == "off"
    finally:
        src.stop()


def test_a_piece_switch_replaces_the_child_after_the_old_one_exits(lib):
    src, _ = make_sources(lib, health=Health())
    try:
        first = src.start_test("piano", "file:Jazz_-_Some_Tune.mid")["pid"]
        assert wait_for(src, lambda: "fake child" in (lib / "state" / "test-piano.log").read_text())
        st = src.start_test("piano", "scale")
        assert st["piece"] == "scale" and st["state"] == "starting"
        assert wait_for(src, lambda: src.status()["piano"]["pid"] not in (None, first))
        assert wait_for(src, lambda: "--test-piano scale" in (lib / "state" / "test-piano.log").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(first, 0)                                           # the old one is gone (and reaped)
    finally:
        src.stop()


def test_a_child_that_ignores_sigterm_is_killed_after_2_s(lib, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "stubborn")
    now = [100.0]
    src, _ = make_sources(lib, health=Health(), clock=lambda: now[0])
    try:
        src.start_test("piano", "chords")
        log_path = lib / "state" / "test-piano.log"
        assert wait_for(src, lambda: "fake child" in log_path.read_text())    # its SIGTERM handler is set
        time.sleep(0.2)
        proc = src._cur["piano"].proc
        src.stop_test("piano")
        time.sleep(0.3)
        src.tick(0.0)
        assert proc.poll() is None and src.status()["piano"]["state"] == "stopping"   # SIGTERM ignored
        now[0] += sources.KILL_AFTER_S + 0.1
        src.tick(0.0)
        assert proc.wait(timeout=3.0) == -signal.SIGKILL
        assert wait_for(src, lambda: src.status()["piano"]["state"] == "off")
    finally:
        src.stop()


def test_audio_test_ranks_first_while_it_plays(lib):
    src, rec = make_sources(lib, health=Health())
    try:
        st = src.start_test("audio")                                    # no piece: the first
        assert st["piece"] == "file:house_y.ogg" and st["stream"] == "test-audio"
        assert rec.priority[-1] == ("test-audio",) and src.snapshot()["priority"] == ["test-audio"]
        assert names(rec.senders[-1]) == ["kiosk", "test-audio"]
        assert wait_for(src, lambda: "fake child" in (lib / "state" / "test-audio.log").read_text())
        text = (lib / "state" / "test-audio.log").read_text()
        assert f"--file {lib / 'music' / 'house_y.ogg'} --port 40001 --bind 127.0.0.1 --stream test-audio" in text
        src.stop_test()
        assert rec.priority[-1] == () and names(rec.senders[-1]) == ["kiosk"]
    finally:
        src.stop()
    norank = settings_for(lib, rank_first=False)
    src, rec = make_sources(lib, health=Health(), settings=norank)
    try:
        src.start_test("audio", "file:jazz-x.mp3")
        assert rec.priority == [()] or all(p == () for p in rec.priority)
    finally:
        src.stop()


def test_playing_needs_a_packet_after_the_child_started(lib):
    now = [50.0]
    health = Health()
    health.streams["test-piano"] = {"state": "streaming", "net": {"age_ms": 100.0}}
    health.t_ms = 49_900.0                                              # last packet at 49.8 s: an earlier run's
    src, _ = make_sources(lib, health=health, clock=lambda: now[0])
    try:
        src.start_test("piano", "chords")
        src.tick(0.0)
        assert src.status()["piano"]["state"] == "starting"
        health.t_ms = 50_500.0                                           # last packet at 50.4 s: ours
        src.tick(0.0)
        assert src.status()["piano"]["state"] == "playing"
    finally:
        src.stop()


def test_stop_ends_every_child(lib):
    src, rec = make_sources(lib, health=Health())
    src.start_test("piano", "chords")
    src.start_test("audio", "file:jazz-x.mp3")
    procs = [c.proc for c in src._cur.values()]
    assert all(p is not None and p.poll() is None for p in procs)
    src.stop()
    assert all(p.poll() is not None for p in procs)
    assert {s["state"] for s in src.status().values()} == {"off"}
    assert names(rec.senders[-1]) == ["kiosk"]


def test_fast_polling_on_the_brain_loop(lib):
    health = Health()
    health.streams["test-piano"] = {"state": "streaming", "net": {"age_ms": 5.0}}
    loop = asyncio.new_event_loop()
    src, _ = make_sources(lib, health=health, loop=loop)

    async def run():
        src.start_test("piano", "chords")
        for _ in range(50):                                             # no tick: only the call_later polls
            if src.status()["piano"]["state"] == "playing":
                return True
            await asyncio.sleep(0.05)
        return False

    try:
        assert loop.run_until_complete(run())
    finally:
        src.stop()
        loop.close()


def test_sources_main_dispatches_to_the_test_audio_child(tmp_path):
    assert sources.main([]) == 2
    assert sources.main(["audio", "--file", str(tmp_path / "missing.wav"), "--port", "0"]) == 2


# -- the test-audio child ------------------------------------------------------------------------------------------------
def make_wav(path: pathlib.Path, seconds: float = 0.5, rate: int = 44100, channels: int = 2) -> pathlib.Path:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc=0.5*sin(2*PI*440*t):s={rate}:d={seconds}", "-ac", str(channels), str(path)],
                   check=True, timeout=30)
    return path


class FakeClock:
    def __init__(self, t_ns: int = 1_000_000_000_000) -> None:
        self.t = t_ns

    def __call__(self) -> int:
        return self.t

    def advance(self, s: float) -> None:
        self.t += int(s * 1e9)


@pytest.fixture
def udp_pair():
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.bind(("127.0.0.1", 0))
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(0.2)
    yield tx, rx
    tx.close()
    rx.close()


def drain(sock: socket.socket, wait: float = 0.05) -> list:
    """Everything queued on ``sock`` (read often: a loopback socket holds only ~160 small datagrams)."""
    out = []
    sock.settimeout(wait)
    while True:
        try:
            out.append(vban.parse(sock.recvfrom(2048)[0]))
        except (socket.timeout, BlockingIOError):
            return out


@needs_ffmpeg
def test_decoder_loops_and_fails_on_a_file_with_no_audio(tmp_path):
    wav = make_wav(tmp_path / "short.wav", seconds=0.01, rate=48000, channels=1)        # 480 frames
    dec = test_audio.Decoder(wav, 1)
    try:
        blocks = [dec.read(128) for _ in range(12)]                     # 1536 frames: 3 loops
        assert all(b.shape == (128, 1) and b.dtype == np.float32 for b in blocks)
        assert dec.loops >= 3 and np.abs(np.concatenate(blocks)).max() > 0.1
    finally:
        dec.close()
    junk = tmp_path / "junk.wav"
    junk.write_text("not audio at all")
    dec = test_audio.Decoder(junk, 1)
    with pytest.raises(test_audio.DecodeError):
        dec.prime()
    dec.close()


@needs_ffmpeg
def test_prefetch_decodes_ahead_never_waits_and_reports_failure(tmp_path):
    wav = make_wav(tmp_path / "p.wav", seconds=0.05, rate=48000, channels=1)
    pre = test_audio.Prefetch(test_audio.Decoder(wav, 1), seconds=0.1).start()
    try:
        deadline = time.monotonic() + 5.0
        while not pre.q.full() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pre.q.full()                                             # 0.1 s ahead, then it waits (paused)
        blocks = [pre.get() for _ in range(pre.q.maxsize)]
        assert all(b is not None and b.shape == (128, 1) for b in blocks)
        while pre.q.qsize():
            pre.get()
    finally:
        pre.close()
    assert not pre._thread.is_alive() and pre.dec.proc is None
    junk = tmp_path / "junk.wav"
    junk.write_text("not audio")
    pre = test_audio.Prefetch(test_audio.Decoder(junk, 1)).start()
    try:
        deadline = time.monotonic() + 5.0
        while pre.error is None and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(test_audio.DecodeError):
            pre.get()
    finally:
        pre.close()


def test_an_underrun_goes_out_as_silence_on_time(tmp_path, udp_pair):
    tx, rx = udp_pair

    class Dry:                                                          # a decoder that never keeps up
        channels, path = 1, pathlib.Path("dry.wav")

        def get(self):
            return None

    clock = FakeClock()
    snd = test_audio.AudioFileSender(tx, Dry(), clock_ns=clock)
    tx.setblocking(False)
    rx.sendto(vban.subscribe_packet(["test-audio"], 0), tx.getsockname())
    time.sleep(0.05)
    snd.step(threading.Event())
    audio = [p for p in drain(rx) if isinstance(p, vban.AudioPacket)]
    assert len(audio) == test_audio.AHEAD + 1 and snd.underruns == len(audio)
    assert all(not p.pcm.any() for p in audio)


@needs_ffmpeg
def test_sender_paces_subscribes_heartbeats_pauses_and_resumes(tmp_path, udp_pair):
    tx, rx = udp_pair
    clock = FakeClock()
    dec = test_audio.Decoder(make_wav(tmp_path / "a.wav", seconds=0.3), 1)
    snd = test_audio.AudioFileSender(tx, dec, clock_ns=clock)
    tx.setblocking(False)
    stop = threading.Event()
    port = tx.getsockname()
    try:
        assert snd.step(stop) == test_audio.IDLE_POLL_S and not drain(rx)            # nobody subscribed
        rx.sendto(vban.subscribe_packet(["room-mic"], 0), port)                       # not our stream: ignored
        snd.step(stop)
        assert not drain(rx)
        rx.sendto(vban.subscribe_packet(["test-audio"], 1), port)
        time.sleep(0.05)
        sent_k, pkts = [], []
        for _ in range(60):                                             # 60 wakes on the paced schedule
            wait = snd.step(stop)
            assert snd.due_ns(snd.k - 1) <= clock() + test_audio.AHEAD * test_audio.PKT_S * 1e9 + 1   # <= 4 ahead
            sent_k.append(snd.k)
            clock.advance(max(wait, 0.001))
            pkts += drain(rx, 0.002)
        audio = [p for p in pkts if isinstance(p, vban.AudioPacket)]
        hbs = [vban.parse_heartbeat(p) for p in pkts if isinstance(p, vban.TextPacket)]
        assert [p.counter for p in audio] == list(range(len(audio)))
        assert all(p.stream == "test-audio" and p.sr == 48000 and p.channels == 1 and p.frames == 128 for p in audio)
        elapsed = (clock() - snd.t0_ns) / 1e9
        assert abs(len(audio) * test_audio.PKT_S - elapsed) <= 5 * test_audio.PKT_S   # real time, 4 ahead at most
        bursts = np.diff([0] + sent_k)
        assert max(bursts) <= 5 and np.median(bursts[1:]) >= 3                      # bursts of about 4
        assert hbs and all(h is not None and h.sess == snd.sess and h.state == "ok" for h in hbs)
        assert hbs[-1].frame is not None and hbs[-1].dev == "file:a.wav"
        first_sess = snd.sess
        clock.advance(test_audio.SUB_TTL_S + 0.1)                       # no SUBSCRIBE for 5 s: pause
        snd.step(stop)
        k_paused = snd.n
        clock.advance(1.0)
        snd.step(stop)
        assert snd.n == k_paused and not [p for p in drain(rx) if isinstance(p, vban.AudioPacket)]
        rx.sendto(vban.subscribe_packet(["test-audio"], 2), port)       # back: a new sess from counter 0
        time.sleep(0.05)
        snd.step(stop)
        again = [p for p in drain(rx) if isinstance(p, vban.AudioPacket)]
        assert again and again[0].counter == 0 and snd.sess != first_sess and snd.sessions == 2
        clock.advance(0.5)                                               # stalled 500 ms: a new sess, not a burst
        snd.step(stop)
        assert snd.sessions == 3 and snd.xruns == 1
        assert len([p for p in drain(rx) if isinstance(p, vban.AudioPacket)]) <= test_audio.AHEAD + 1
    finally:
        dec.close()


@needs_ffmpeg
def test_test_audio_child_streams_to_the_receiver_over_localhost(tmp_path):
    """The real child process (ffmpeg decode, SUBSCRIBE, pacing, heartbeat) into show_service's Receiver."""
    wav = make_wav(tmp_path / "tone.wav", seconds=0.7)                    # 44.1 kHz stereo -> 48 kHz mono
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-m", "show_service.test_audio", "--file", str(wav), "--port", "0",
                             "--bind", "127.0.0.1"], cwd=str(contract.REPO_ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
    rx = None
    try:
        line, deadline = "", time.monotonic() + 10.0
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            m = re.search(r"on udp 127\.0\.0\.1:(\d+)", line)
            if m:
                break
        assert m, f"no port line from the child: {line!r}"
        port = int(m.group(1))
        blocks: list[tuple] = []
        rx = Receiver(Config(senders=(Sender("ta", "127.0.0.1", port, ("test-audio",)),),
                             vban_bind=("127.0.0.1", 0)),
                      on_audio=lambda s, n0, t0, pcm, fs, fl: blocks.append((s, n0, t0, pcm, fs, fl)),
                      on_midi=lambda *a: None, on_tick=lambda now: None)
        rx.start()
        t_start = time.monotonic()
        deadline = t_start + 10.0
        while time.monotonic() < deadline:
            st = rx.health().get("streams", {}).get("test-audio", {})
            if sum(len(b[3]) for b in blocks) >= 1.5 * 48000 and (st.get("sender") or {}).get("sess"):
                break
            time.sleep(0.05)
        elapsed = time.monotonic() - t_start
        h = rx.health()["streams"]["test-audio"]
        assert h["kind"] == "audio" and h["test"] and h["state"] == "streaming"
        assert h["format"]["sr"] == 48000 and h["format"]["channels"] == 1
        assert h["sender"]["dev"] == "file:tone.wav" and h["sender"]["state"] == "ok"
        assert rx.active_audio() == "test-audio"
        assert blocks[0][5] & AUDIO_SEGMENT and blocks[0][4] == 48000
        runs: list[list[tuple[int, int]]] = []                             # (n0, frames) per receiver segment
        for b in blocks:                                                   # (a block may hold several packets)
            if b[5] & AUDIO_SEGMENT:
                runs.append([])
            runs[-1].append((b[1], len(b[3])))
        assert all(r[0][0] == 0 and all(r[i + 1][0] == r[i][0] + r[i][1] for i in range(len(r) - 1))
                   for r in runs)                                          # contiguous from 0, nothing lost
        assert len(runs) <= 3                                              # a new sess only if the child stalled
        assert sum(len(b[3]) for b in blocks) / 48000 <= elapsed + 0.2     # paced in real time, not a flood
        pcm = np.concatenate([b[3] for b in blocks])
        assert np.abs(pcm).max() > 0.1 and all(math.isfinite(b[2]) for b in blocks)   # the tone, looping past 0.7 s
    finally:
        if rx is not None:
            rx.stop()
        proc.send_signal(signal.SIGTERM)
        try:
            code = proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=5.0)
        rest = proc.stderr.read()
        proc.stderr.close()
    assert code == 0, rest
    assert "stopped:" in rest


@needs_ffmpeg
def test_test_audio_child_exits_1_on_a_file_that_wont_decode(tmp_path):
    junk = tmp_path / "junk.mp3"
    junk.write_text("nope")
    r = subprocess.run([sys.executable, "-m", "show_service.test_audio", "--file", str(junk), "--port", "0"],
                       cwd=str(contract.REPO_ROOT), capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "gave no audio" in r.stderr


# -- tools/show/fetch_test_music.py ---------------------------------------------------------------------------------------
def load_fetch():
    path = contract.REPO_ROOT / "tools" / "show" / "fetch_test_music.py"
    spec = importlib.util.spec_from_file_location("_fetch_test_music_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_sources_are_complete_licensed_and_small():
    f = load_fetch()
    assert f.check_sources(f.SOURCES) == []
    vocab = yaml.safe_load((contract.REPO_ROOT / "config" / "live" / "vocab.yaml").read_text(encoding="utf-8"))
    assert set(vocab["genre"]) <= {s["genre"] for s in f.SOURCES}          # at least one per vocab genre
    assert 6 <= len(f.SOURCES) <= 10
    assert sum(s["bytes"] for s in f.SOURCES) < 80e6
    for s in f.SOURCES:
        assert s["license"] in ("CC0", "CC BY", "CC BY-SA", "public domain")
        assert re.search(r"creativecommons\.org/(licenses/(by|by-sa)|publicdomain/(zero|mark))/", s["license_url"])
        assert s["url"].startswith("https://") and s["page"].startswith("https://")
        assert isinstance(s["sha256"], str) and len(s["sha256"]) == 64
    sources_md = (contract.REPO_ROOT / "config" / "live" / "test-music" / "SOURCES.md").read_text(encoding="utf-8")
    for s in f.SOURCES:
        assert f"`{s['id']}." in sources_md                               # every track is listed with its licence


def test_fetch_list_needs_no_network(monkeypatch, capsys):
    f = load_fetch()
    monkeypatch.setattr(f.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    assert f.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert all(s["id"] in out for s in f.SOURCES)
    assert f.main(["--list", "--only", "nope"]) == 2


def test_fetch_downloads_checks_and_is_idempotent(tmp_path, capsys):
    f = load_fetch()
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "Track One.ogg").write_bytes(b"OggS" + bytes(range(256)) * 40)
    data = (origin / "Track One.ogg").read_bytes()
    good = {"id": "jazz-one", "title": "One", "artist": "A", "genre": "jazz", "license": "CC BY",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "url": (origin / "Track One.ogg").as_uri(), "page": "https://example.org/one",
            "credit": "One by A", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    bad = dict(good, id="rock-two", genre="rock", sha256="0" * 64)
    dest = tmp_path / "music"
    assert f.main(["--dir", str(dest)], sources=[good, bad]) == 1          # one failure
    assert (dest / "jazz-one.ogg").read_bytes() == data
    assert not (dest / "rock-two.ogg").exists() and not list(dest.glob("*.part")) and not list(dest.glob("*.new"))
    index = json.loads((dest / "index.json").read_text())
    assert set(index) == {"jazz-one"}
    e = index["jazz-one"]
    assert e["file"] == "jazz-one.ogg" and e["sha256"] == good["sha256"] and e["bytes"] == len(data)
    assert e["license"] == "CC BY" and e["page"] == "https://example.org/one" and e["fetched_at"]
    capsys.readouterr()
    assert f.main(["--dir", str(dest), "--only", "jazz-one"], sources=[good, bad]) == 0
    assert "already here" in capsys.readouterr().out
    assert f.main(["--dir", str(dest), "--only", "rock-two", "--no-verify"], sources=[good, bad]) == 0
    assert (dest / "rock-two.ogg").exists() and set(json.loads((dest / "index.json").read_text())) == {"jazz-one",
                                                                                                         "rock-two"}
    # what show_service lists from that folder
    pieces = sources.audio_pieces(dest)
    assert [p["label"] for p in pieces] == ["One", "One"] and pieces[0]["genre"] == "jazz"
    assert f.ext_of("https://x.org/a/b.flac/b.flac.ogg") == "ogg" and f.ext_of("https://x.org/a%20b.mp3") == "mp3"


def test_children_run_under_the_parent_death_shim_and_are_killed_with_their_group(lib, monkeypatch):
    """A child leads its own process group (start_new_session): SIGKILL goes to the group, so a grandchild (test
    audio's ffmpeg) goes too. Each child runs through PDEATH_SHIM, which execs the real program with its argv."""
    monkeypatch.setenv("FAKE_MODE", "stubborn")
    script = lib / "fake_child.py"
    script.write_text(FAKE_CHILD.replace('mode = os.environ.get("FAKE_MODE", "run")',
                                         'mode = os.environ.get("FAKE_MODE", "run")\n'
                                         'import subprocess\n'
                                         'kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                                         'print("grandchild", kid.pid, flush=True)'))
    now = [100.0]
    src, _ = make_sources(lib, health=Health(), clock=lambda: now[0])
    try:
        src.start_test("piano", "chords")
        log_path = lib / "state" / "test-piano.log"
        assert wait_for(src, lambda: "grandchild" in log_path.read_text())
        text = log_path.read_text()
        assert text.splitlines()[0].startswith(sources.LOG_HEADER) and "-c" not in text.splitlines()[0]
        assert "fake child --test-piano chords" in text                  # the shim exec'd the real argv
        kid = int(next(ln for ln in text.splitlines() if ln.startswith("grandchild")).split()[1])
        proc = src._cur["piano"].proc
        src.stop_test("piano")
        time.sleep(0.2)
        now[0] += sources.KILL_AFTER_S + 0.1
        src.tick(0.0)
        assert proc.wait(timeout=3.0) == -signal.SIGKILL
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(kid, 0)
            except ProcessLookupError:
                break
            try:                                                        # reaped by init, or a zombie of ours
                if pathlib.Path(f"/proc/{kid}/stat").read_text().split()[2] == "Z":
                    break
            except OSError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the grandchild outlived its group's SIGKILL")
    finally:
        src.stop()


def test_the_shim_exits_when_its_parent_is_gone():
    """PDEATH_SHIM checks the parent it was given: a stale pid (the parent already gone) exits without running."""
    out = subprocess.run([sys.executable, "-c", sources.PDEATH_SHIM, "1", sys.executable, "-c", "print('ran')"],
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 0 and "ran" not in out.stdout
    out = subprocess.run([sys.executable, "-c", sources.PDEATH_SHIM, str(os.getpid()), sys.executable, "-c",
                          "print('ran')"], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0 and out.stdout.strip() == "ran"
