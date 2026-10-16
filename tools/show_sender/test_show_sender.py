"""show_sender: MIDI parsing and batching, audio counters over overflows, SUBSCRIBE, and a UDP loopback of the
test piano, every packet checked with show_service/vban.py's own parser."""
from __future__ import annotations

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
