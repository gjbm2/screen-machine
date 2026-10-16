"""VBAN receiver (show_service/receiver.py): ordering, holes, segments, heartbeats, MIDI, states, health and
SUBSCRIBE (docs/SHOW_ENGINE.md §3.1, §3.3, §3.5; README "receiver.py").

Most tests drive ``handle()`` / ``tick()`` with packed bytes and fake times. The socket tests use ephemeral ports on
127.0.0.1 only (an in-process sender), never 6980 or any real device.
"""

from __future__ import annotations

import math
import socket
import threading
import time

import numpy as np
import pytest

from show_service import contract, vban
from show_service.contract import AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, Config, Sender
from show_service.receiver import Receiver

FS, FR = 48000, 128
PKT_S = FR / FS
ADDR = ("192.0.2.10", 40000)          # TEST-NET-1: only ever a tuple here, nothing is sent to it
T0 = 1000.0
LAT = 0.003
N_OFF = 1_000_000                     # the sender's own sample index is far from ours
CAP0 = 5_000_000_000_000              # the sender's clock (ns)
HB_EVERY = 187                        # packets per 0.5 s


class Rec:
    def __init__(self) -> None:
        self.audio: list[tuple] = []
        self.midi: list[tuple] = []
        self.ticks: list[float] = []

    def on_audio(self, stream, n0, t0, pcm, fs, flags) -> None:
        self.audio.append((stream, n0, t0, pcm, fs, flags))

    def on_midi(self, stream, msg, arrival, to_host) -> None:
        self.midi.append((stream, msg, arrival, to_host))

    def on_tick(self, now) -> None:
        self.ticks.append(now)

    def of(self, stream: str) -> list[tuple]:
        return [b for b in self.audio if b[0] == stream]


def make(cfg: Config | None = None, **kw) -> tuple[Receiver, Rec]:
    rec = Rec()
    rx = Receiver(cfg or Config(), on_audio=rec.on_audio, on_midi=rec.on_midi, on_tick=rec.on_tick, **kw)
    return rx, rec


def val(c: int) -> float:
    return ((c % 50) + 1) / 100.0


def apkt(c: int, stream: str = "room-mic", frames: int = FR, ch: int = 1, v: float | None = None, sr: int = FS,
         fmt: int = vban.INT16) -> bytes:
    return vban.pack_audio(stream, c, np.full((frames, ch), val(c) if v is None else v, np.float32), sr, fmt)


def hbpkt(stream: str, counter: int, **kw) -> bytes:
    kw.setdefault("frame", None)
    kw.setdefault("n", None)
    kw.setdefault("t_cap_ns", None)
    return vban.pack_text(stream, counter, vban.make_heartbeat(**kw))


class Sim:
    """A sender. Packet i (its own time index) goes out with wire counter ``i + c_off``; its first sample is the
    sender's index N_OFF + i*FR (+ n_extra), captured at CAP0 + i*FR/FS s (+ cap_extra_ns); it arrives at
    T0 + (i + 1) * PKT_S + LAT (+ arr_extra). A heartbeat follows every HB_EVERY packets."""

    def __init__(self, rx: Receiver, stream: str = "room-mic", addr=ADDR, sess: str = "A", t0: float = T0,
                 v: float | None = None) -> None:
        self.rx, self.stream, self.addr, self.sess, self.t0, self.v = rx, stream, addr, sess, t0, v
        self.i = 0
        self.c_off = 0
        self.hb_c = 0
        self.next_hb = 0
        self.cap_extra_ns = 0
        self.arr_extra = 0.0
        self.n_extra = 0
        self.xruns = 0
        self.hb_state = "ok"
        self.heartbeats = True

    def arrival(self, i: int) -> float:
        return self.t0 + (i + 1) * PKT_S + LAT + self.arr_extra

    def tcap(self, i: int) -> int:
        return CAP0 + int(round(i * FR / FS * 1e9)) + self.cap_extra_ns

    def heartbeat(self, i: int, at: float) -> None:
        self.rx.handle(hbpkt(self.stream, self.hb_c, sess=self.sess, frame=(i + self.c_off) % (1 << 32),
                             n=N_OFF + i * FR + self.n_extra, t_cap_ns=self.tcap(i), xruns=self.xruns,
                             state=self.hb_state, dev="test mic"), self.addr, at)
        self.hb_c += 1

    def run(self, n: int = 1, skip=(), tick: bool = True) -> float:
        t = self.t0
        for _ in range(n):
            i = self.i
            self.i += 1
            t = self.arrival(i)
            if i not in skip:
                self.rx.handle(apkt((i + self.c_off) % (1 << 32), self.stream, v=self.v), self.addr, t)
            if self.heartbeats and i >= self.next_hb:
                self.heartbeat(i, t + 0.0005)
                self.next_hb = i + HB_EVERY
            if tick:
                self.rx.tick(t)
        return t

    def seconds(self, s: float, **kw) -> float:
        return self.run(int(round(s / PKT_S)), **kw)


def spy_segments(rx: Receiver, stream: str = "room-mic") -> list:
    clock = rx._streams[stream].clock
    calls: list = []
    orig = clock.segment

    def spy(reason="", **kw):
        calls.append((reason, kw))
        return orig(reason, **kw)
    clock.segment = spy
    return calls


def net(rx: Receiver, stream: str = "room-mic") -> dict:
    rx._build_health(rx._streams[stream].last_data_s or 0.0)
    return rx.health()["streams"][stream]["net"]


# -- in order, holes, duplicates --------------------------------------------------------------------------------------
def test_in_order_blocks_are_contiguous_and_timed():
    rx, rec = make()
    s = Sim(rx, v=None)
    s.heartbeats = False
    s.run(10)
    b = rec.of("room-mic")
    assert [x[1] for x in b] == [i * FR for i in range(10)]
    assert b[0][5] == AUDIO_SEGMENT and all(x[5] == 0 for x in b[1:])
    assert all(x[4] == FS and x[3].shape == (FR, 1) and x[3].dtype == np.float32 for x in b)
    assert [round(float(x[3][0, 0]), 3) for x in b] == [round(val(i), 3) for i in range(10)]
    t0 = np.array([x[2] for x in b])
    assert np.allclose(np.diff(t0), PKT_S, atol=1e-6)
    assert t0[0] == pytest.approx(T0 + LAT, abs=1e-6)          # sample 0's capture + the transport delay
    assert rx.active_audio() == "room-mic"


def test_reordered_packet_fills_the_hole_within_the_hold():
    rx, rec = make()
    s = Sim(rx)
    s.heartbeats = False
    s.run(2)
    t3 = s.arrival(3)
    rx.handle(apkt(3), ADDR, t3)
    rx.tick(t3)
    assert len(rec.audio) == 2                                  # 3 is held for the hole at 2
    rx.handle(apkt(2), ADDR, t3 + 0.001)
    assert len(rec.audio) == 4
    s.i = 4
    s.run(2)
    b = rec.audio
    assert [x[1] for x in b] == [i * FR for i in range(6)]
    assert [round(float(x[3][0, 0]), 3) for x in b] == [round(val(i), 3) for i in range(6)]
    assert all(x[5] & AUDIO_GAP == 0 for x in b)
    assert net(rx)["gaps"] == 0 and net(rx)["lost"] == 0


@pytest.mark.parametrize("dropped", [1, 3])
def test_hole_is_zero_filled_after_the_hold(dropped):
    rx, rec = make()
    s = Sim(rx)
    s.heartbeats = False
    s.run(5)
    lost = set(range(5, 5 + dropped))
    nxt = 5 + dropped
    t = s.arrival(nxt)
    rx.handle(apkt(nxt), ADDR, t)
    rx.tick(t + 0.003)                                          # hold = 4 ms (no jitter yet): still waiting
    assert len(rec.audio) == 5
    rx.tick(t + 0.0045)
    assert len(rec.audio) == 7
    gap, resumed = rec.audio[5], rec.audio[6]
    assert gap[5] == AUDIO_GAP and gap[1] == 5 * FR and gap[3].shape == (dropped * FR, 1) and not gap[3].any()
    assert gap[4] == FS and gap[2] == pytest.approx(T0 + LAT + 5 * FR / FS, abs=1e-6)
    assert resumed[5] == AUDIO_RESUME and resumed[1] == nxt * FR
    s.i = nxt + 1
    s.run(3)
    assert [x[5] for x in rec.audio[7:]] == [0, 0, 0]
    n = net(rx)
    assert (n["gaps"], n["lost"]) == (1, dropped) and n["loss_pct"] > 0
    # the lost packet turns up after all: too late, counted, dropped
    rx.handle(apkt(min(lost)), ADDR, s.arrival(s.i))
    assert len(rec.audio) == 10 and net(rx)["late"] == 1


def test_hole_hold_waits_for_the_next_tick_without_packets():
    rx, rec = make()
    s = Sim(rx)
    s.heartbeats = False
    s.run(3)
    rx.handle(apkt(4), ADDR, s.arrival(4))
    assert len(rec.audio) == 3
    rx.tick(s.arrival(4) + 0.05)                                # the receive loop's tick expires it
    assert [x[5] for x in rec.audio[3:]] == [AUDIO_GAP, AUDIO_RESUME]


def test_duplicates_are_dropped_and_counted():
    rx, rec = make()
    s = Sim(rx)
    s.heartbeats = False
    s.run(3)
    rx.handle(apkt(2), ADDR, s.arrival(3))
    rx.handle(apkt(0), ADDR, s.arrival(3))
    s.run(2)
    assert len(rec.audio) == 5 and net(rx)["dup"] == 2


# -- segments ---------------------------------------------------------------------------------------------------------
def test_counter_discontinuity_starts_a_counter_segment():
    rx, rec = make()
    s = Sim(rx)
    s.heartbeats = False
    s.run(5)
    calls = spy_segments(rx)
    s.c_off = 1000
    s.run(3)
    assert calls == [("counter", {"keep_skew": False})]         # no heartbeat: no sess to vouch for the device
    b = rec.audio[5]
    assert b[1] == 0 and b[5] == AUDIO_SEGMENT
    assert rx._streams["room-mic"].clock.reason == "counter"
    s.c_off = -(5 + 1000)                                       # a counter reset (backwards): a segment too
    s.run(2)
    assert [c[0] for c in calls] == ["counter", "counter"] and rec.audio[-2][1] == 0


def test_counter_and_idle_segments_keep_skew_with_an_unchanged_sess():
    rx, rec = make()
    s = Sim(rx)
    rx.handle(hbpkt("room-mic", 0, sess="A"), ADDR, T0)       # heartbeat (no audio yet) first
    s.hb_c = 1
    s.run(400)
    calls = spy_segments(rx)
    s.c_off = 5000
    s.run(400)
    s.arr_extra = 1.5                                           # 1.5 s silence: idle
    s.run(10)
    assert calls == [("counter", {"keep_skew": True}), ("idle", {"keep_skew": True})]


def test_sess_change_segments_once():
    rx, rec = make()
    s = Sim(rx)
    s.run(400)
    calls = spy_segments(rx)
    s.sess = "B"                                                # device re-opened, counters continuous
    s.next_hb = s.i
    s.run(5)
    assert calls == [("sess", {"keep_skew": True})]
    seg = [x for x in rec.audio if x[5] & AUDIO_SEGMENT]
    assert len(seg) == 2 and seg[-1][1] == 0
    # a sender restart: the counter jumps first, the new sess's heartbeat follows: one segment, not two
    calls.clear()
    s.c_off = 77777
    s.sess = "C"
    s.next_hb = s.i + 50
    s.run(200)
    assert [c[0] for c in calls] == ["counter"]


def test_format_change_starts_a_format_segment_with_the_new_rate():
    rx, rec = make()
    rx.handle(apkt(0), ADDR, T0)
    rx.handle(apkt(1), ADDR, T0 + PKT_S)
    calls = spy_segments(rx)
    rx.handle(apkt(2, frames=64), ADDR, T0 + 2 * PKT_S)
    rx.handle(vban.pack_audio("room-mic", 3, np.zeros((128, 1), np.float32), 44100), ADDR, T0 + 3 * PKT_S)
    assert [c[0] for c in calls] == ["format", "format"] and calls[-1][1].get("fs") == 44100
    assert [(x[1], x[4], x[5]) for x in rec.audio[2:]] == [(0, FS, AUDIO_SEGMENT), (0, 44100, AUDIO_SEGMENT)]


def test_heartbeat_idle_state_ending_starts_an_idle_segment():
    rx, rec = make()
    s = Sim(rx)
    s.run(200)
    calls = spy_segments(rx)
    s.hb_state = "idle"
    s.next_hb = s.i
    s.run(200)
    assert calls == []
    s.hb_state = "ok"
    s.next_hb = s.i
    s.run(3)
    assert calls == [("idle", {"keep_skew": True})]


# -- heartbeats: CaptureMap and sender steps --------------------------------------------------------------------------
def test_heartbeats_feed_the_capture_map_without_false_steps():
    rx, rec = make()
    s = Sim(rx)
    s.seconds(10.0)
    st = rx._streams["room-mic"]
    assert st.capture.ready and st.capture.seg == st.clock.seg
    assert st.sender_steps == 0 and st.segments == 1
    assert st.capture.to_n(s.tcap(1000)) == pytest.approx(1000 * FR, abs=1.0)
    # a counter segment restarts our n at 0: the n - n_rx baseline is re-taken, no false sender step
    s.c_off = 100000
    s.seconds(5.0)
    assert st.sender_steps == 0 and st.segments == 2 and st.capture.ready
    # ... and the same after a zero-filled gap
    s.seconds(1.0, skip={s.i + 3, s.i + 4})
    s.seconds(2.0)
    assert st.sender_steps == 0 and st.gaps == 1


@pytest.mark.parametrize("trigger", ["capture", "xruns", "n"])
def test_sender_step_triggers(trigger):
    rx, rec = make()
    s = Sim(rx)
    s.seconds(6.0)
    st = rx._streams["room-mic"]
    calls = spy_segments(rx)
    if trigger == "capture":                                    # 20 ms of capture lost, counting on
        s.cap_extra_ns = 20_000_000
        s.arr_extra = 0.020
    elif trigger == "xruns":
        s.xruns = 1
    else:                                                       # samples skipped, counter continuous
        s.n_extra = 3 * FR
    s.seconds(1.2)
    assert [c[0] for c in calls] == ["sender-step"] and calls[0][1] == {"keep_skew": True}
    assert st.sender_steps == 1 and st.clock.reason == "sender-step"
    s.seconds(3.0)                                              # and it settles: no more steps
    assert st.sender_steps == 1 and st.capture.ready


# -- MIDI -------------------------------------------------------------------------------------------------------------
def midi_batch(seq: int, t_ns: int, counter: int, stream: str = "room-piano", sess: str = "m") -> bytes:
    return vban.pack_midi_events(stream, counter, vban.midi_events_obj(seq, [[t_ns, 0x90, 60, 80]], sess=sess))


def test_midi_is_dispatched_with_to_host_on_the_audio_map():
    rx, rec = make()
    s = Sim(rx)
    s.seconds(3.0)
    midi_addr = (ADDR[0], ADDR[1] + 1)                          # same box, another port
    for k in range(5):
        i = s.i
        t_ns = s.tcap(i)
        rx.handle(midi_batch(k + 1, t_ns, k), midi_addr, s.arrival(i) + 0.001)
        s.run(20)
    stream, msg, arrival, to_host = rec.midi[-1]
    assert stream == "room-piano" and isinstance(msg, vban.MidiBatch) and msg.seq == 5
    tm = rx._streams["room-piano"].tmap
    assert tm.via == "audio" and rx._streams["room-piano"].attached == "room-mic"
    t_ns = msg.ev[0][0]
    assert to_host(t_ns) == pytest.approx(T0 + LAT + (t_ns - CAP0) / 1e9, abs=5e-4)
    # a state too, and duplicates pass through (midi.py dedupes)
    st_obj = vban.midi_state_obj(5, [0] * 88, 0, 0, 0, s.tcap(s.i), sess="m")
    rx.handle(vban.pack_text("room-piano", 5, st_obj), midi_addr, s.arrival(s.i))
    rx.handle(midi_batch(5, t_ns, 4), midi_addr, s.arrival(s.i))
    assert isinstance(rec.midi[-2][1], vban.MidiState) and rec.midi[-1][1].seq == 5
    # offsets: the MIDI stream's own
    before = to_host(t_ns)
    rx.set_offsets({"room-piano": 25.0})
    rx.tick(s.arrival(s.i))
    assert rec.midi[-1][3](t_ns) == pytest.approx(before + 0.025, abs=1e-6)
    # the audio stream goes down: detached, MIDI keeps its own clock (+ the bridge)
    last = s.arrival(s.i)
    for k in range(70):
        t = last + 0.1 * (k + 1)
        rx.handle(midi_batch(10 + k, s.tcap(s.i) + int((t - last) * 1e9), 10 + k), midi_addr, t + 0.0005)
        rx.tick(t + 0.001)
    assert rx._streams["room-mic"].state == "down"
    assert tm.audio is None and tm.via == "remote" and rx._streams["room-piano"].attached is None


def test_midi_from_another_box_is_not_attached():
    rx, rec = make()
    s = Sim(rx)
    s.seconds(3.0)
    rx.handle(midi_batch(1, s.tcap(s.i), 0), ("192.0.2.99", 40001), s.arrival(s.i))
    assert rx._streams["room-piano"].tmap.audio is None
    assert rec.midi[-1][3](s.tcap(s.i)) == pytest.approx(s.arrival(s.i), abs=1e-9)    # its own clock


# -- registry, states, active stream, health --------------------------------------------------------------------------
def test_unknown_and_test_streams_are_accepted_and_ranked_last():
    rx, rec = make()
    for name in ("mystery", "test-thing", "phone-test"):
        for c in range(3):
            rx.handle(apkt(c, name), ADDR, T0 + c * PKT_S)
    rx.handle(b"not vban", ADDR, T0)
    rx.handle(vban.subscribe_packet(["x"], 0), ADDR, T0)
    rx.handle(vban.pack_text("room-mic", 0, {"v": 1, "what": "?"}), ADDR, T0)
    rx.tick(T0 + 0.01)
    h = rx.health()
    m, t, p = h["streams"]["mystery"], h["streams"]["test-thing"], h["streams"]["phone-test"]
    assert (m["kind"], m["rank"], m["test"]) == ("audio", contract.UNKNOWN_RANK, False)
    assert t["test"] is True and p["test"] is True and p["rank"] < m["rank"]
    assert h["socket"]["junk"] == 2 and h["socket"]["subscribe_rx"] == 1 and h["socket"]["rx"] == 12
    assert h["active_audio"] == "mystery"                       # the first live one: anything beats nothing


def test_stream_priority_from_config_ranks_first():
    rx, rec = make(Config(stream_priority=("pc22-loop",)))
    for name in ("room-mic", "pc22-loop"):
        rx.handle(apkt(0, name), ADDR, T0)
    rx.tick(T0 + 0.01)
    s = rx.health()["streams"]
    assert s["pc22-loop"]["rank"] < s["room-mic"]["rank"] == contract.stream_rank("room-mic")


def test_health_shape_and_states():
    rx, rec = make()
    s = Sim(rx)
    t = s.seconds(2.0)
    h = rx.health()
    assert set(h) >= {"t_ms", "active_audio", "socket", "senders", "streams"}
    assert set(h["socket"]) >= {"bind", "error", "rx", "junk", "subscribe_rx", "bytes", "rcvbuf"}
    m = h["streams"]["room-mic"]
    assert set(m) >= {"kind", "rank", "test", "addr", "state", "format", "net", "audio", "sender", "clock", "line"}
    assert m["format"] == {"sr": 48000, "channels": 1, "fmt": "INT16", "frames": 128}
    assert set(m["net"]) >= {"packets", "loss_pct", "late", "dup", "gaps", "jitter_ms", "hold_ms", "drift_ppm",
                             "skew_alarm", "age_ms", "segments"}
    assert set(m["audio"]) == {"peak_dbfs", "rms_dbfs", "clips", "silent", "floor_dbfs", "trace"}
    assert set(m["sender"]) == {"sess", "dev", "state", "xruns", "in_lat_ms", "age_ms", "devices"}
    assert m["state"] == "streaming" and m["addr"] == "192.0.2.10:40000" and m["sender"]["dev"] == "test mic"
    assert m["line"].startswith("room-mic · 48k/1ch · −") and "% loss" in m["line"] and "ms jitter" in m["line"]
    assert -60.0 < m["audio"]["rms_dbfs"] < 0.0 and m["net"]["hold_ms"] == 4.0
    rx.tick(t + 0.7)
    assert rx._streams["room-mic"].state == "stale"
    rx.tick(t + 5.2)
    rx.tick(t + 5.8)
    assert rx.health()["streams"]["room-mic"]["state"] == "down"


def test_quiet_and_no_device_states():
    rx, rec = make()
    s = Sim(rx, v=0.0)
    s.seconds(4.0)
    assert rx._streams["room-mic"].state == "streaming"
    s.seconds(2.0)
    assert rx._streams["room-mic"].state == "quiet" and rx.active_audio() == "room-mic"   # quiet is still live
    assert rx.health()["streams"]["room-mic"]["audio"]["silent"] is True
    s.hb_state = "no_device"
    s.next_hb = s.i
    s.run(40)
    assert rx._streams["room-mic"].state == "no_device"


def test_active_stream_switches_up_after_3_s_and_away_after_1_s():
    rx, rec = make()
    loop = Sim(rx, "pc22-loop", addr=("192.0.2.20", 40000))
    mic = Sim(rx, "room-mic", t0=T0 + 0.5)
    loop.heartbeats = mic.heartbeats = False
    loop.seconds(0.5)
    assert rx.active_audio() == "pc22-loop"
    # both, interleaved by arrival
    end = T0 + 6.0
    while loop.arrival(loop.i) < end:
        first = loop if loop.arrival(loop.i) <= mic.arrival(mic.i) else mic
        first.run(1)
    mic_blocks = rec.of("room-mic")
    assert mic_blocks and mic_blocks[0][5] & AUDIO_SEGMENT
    switched_at = mic_blocks[0][2]                              # (its sound time; arrival is ~LAT later)
    assert 3.0 <= switched_at - (T0 + 0.5) <= 3.3
    assert rx.active_audio() == "room-mic"
    loop_after = [b for b in rec.of("pc22-loop") if b[2] > switched_at]
    assert loop_after == []                                     # only the active stream is delivered
    # the mic stops: back to the loop 1 s after the mic went stale
    stop = mic.arrival(mic.i - 1)
    n = len(rec.audio)
    loop.seconds(3.0)
    back = rec.audio[n:]
    assert back and back[0][0] == "pc22-loop" and back[0][5] & AUDIO_SEGMENT
    assert 1.5 <= back[0][2] - stop <= 1.8
    assert rx.active_audio() == "pc22-loop"


def test_everything_down_means_no_active_stream():
    rx, rec = make()
    s = Sim(rx)
    t = s.seconds(1.0)
    for k in range(1, 21):
        rx.tick(t + 0.1 * k)
    assert rx.active_audio() is None                            # stale at +0.5 s, given up 1 s later
    s.arr_extra = 3.0                                           # it comes back: taken at once, flagged
    s.run(2)
    assert rx.active_audio() == "room-mic" and rec.audio[-2][5] & AUDIO_SEGMENT


def test_set_offsets_moves_the_audio_map():
    rx, rec = make()
    s = Sim(rx)
    s.heartbeats = False
    s.run(5)
    rx.set_offsets({"room-mic": 10.0, "other": 3.0})
    s.run(5)
    clock = rx._streams["room-mic"].clock
    assert clock.acoustic_offset_ms == 10.0
    last = rec.audio[-1]
    assert last[2] == pytest.approx(clock.to_host(last[1], offset=False) + 0.010, abs=1e-9)


def test_callback_errors_are_counted_not_raised():
    def boom(*a):
        raise RuntimeError("consumer bug")
    rx = Receiver(Config(), on_audio=boom, on_midi=boom, on_tick=boom)
    rx.handle(apkt(0), ADDR, T0)
    rx.handle(midi_batch(1, CAP0, 0), ADDR, T0)
    rx.tick(T0 + 0.6)
    rx.tick(T0 + 1.2)
    assert rx.health()["errors"]["audio"] == 1 and rx.health()["errors"]["midi"] == 1
    assert rx._errors["tick"] == 2                               # (the snapshot is taken before on_tick)


# -- SUBSCRIBE and the socket (ephemeral localhost ports only) --------------------------------------------------------
@pytest.fixture
def sender_sock():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    yield s
    s.close()


def test_subscribe_schedule(sender_sock):
    port = sender_sock.getsockname()[1]
    rsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rsock.bind(("127.0.0.1", 0))
    try:
        cfg = Config(senders=(Sender("kiosk", "localhost", port, ("room-mic", "room-piano")),),
                     vban_bind=("127.0.0.1", 0))
        rx, rec = make(cfg, sock=rsock)
        rx.set_off_streams(())                                  # the mic is on (README S5.16): nothing off
        rx.tick(9.0)                                            # not resolved yet: nothing sent
        rx._resolve_all()                                       # (show-dns's job)
        sender_sock.settimeout(1.0)
        rx.tick(9.01)                                           # newly resolved: at once
        data, src = sender_sock.recvfrom(2048)
        pkt = vban.parse(data)
        assert vban.subscribed_streams(pkt) == ["room-mic", "room-piano"] and pkt.stream == "sub"
        assert src == rsock.getsockname() and pkt.counter == 0
        sender_sock.settimeout(0.05)
        rx.tick(9.5)
        with pytest.raises(socket.timeout):
            sender_sock.recvfrom(2048)
        sender_sock.settimeout(1.0)
        rx.tick(10.02)
        assert vban.parse(sender_sock.recvfrom(2048)[0]).counter == 1
        rx.tick(10.6)
        h = rx.health()["senders"]["kiosk"]
        assert h["addr"] == f"127.0.0.1:{port}" and h["resolve_error"] is None
        assert h["last_subscribe_ms"] == 10020.0 and h["streams"] == ["room-mic", "room-piano"]
        assert rx.health()["socket"]["rcvbuf"] > 0
    finally:
        rsock.close()


def test_resolve_failure_keeps_the_last_address(monkeypatch):
    cfg = Config(senders=(Sender("kiosk", "localhost", 6980, ("room-mic",)),))
    rx, rec = make(cfg)
    rx._resolve_all()
    good = rx._resolved[("kiosk", "localhost")]

    def fail(*a, **kw):
        raise OSError("lookup timed out")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    rx._resolve_all()
    assert rx._resolved[("kiosk", "localhost")] == good and rx._resolve_err[("kiosk", "localhost")]


# -- the Stage-3 seam: set_senders, set_priority, the host fallback (README S3.10) ----------------------------------
def _subs(sock: socket.socket, wait: float = 0.05) -> list[list[str]]:
    """Every SUBSCRIBE waiting on ``sock`` (the streams each asks for)."""
    out = []
    sock.settimeout(wait)
    while True:
        try:
            data, _ = sock.recvfrom(2048)
        except (socket.timeout, BlockingIOError):
            return out
        out.append(vban.subscribed_streams(vban.parse(data)))


def test_set_senders_adds_and_removes_senders():
    a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for x in (a, b, rsock):
        x.bind(("127.0.0.1", 0))
    pa, pb = a.getsockname()[1], b.getsockname()[1]
    try:
        rx, rec = make(Config(senders=(Sender("one", "localhost", pa, ("room-mic",)),)), sock=rsock)
        rx.set_off_streams(())                                # the mic is on: nothing off
        rx._resolve_all()
        rx.tick(10.0)
        assert _subs(a) == [["room-mic"]] and _subs(b) == []
        two = Sender("two", "127.0.0.1", pb, ("test-audio",), hosts=("127.0.0.1",), on_demand=True)
        rx.set_senders((Sender("one", "localhost", pa, ("room-mic",)), two))
        assert rx.senders()[0].name == "one" and len(rx.senders()) == 1    # applied at the next tick
        rx._resolve_all()                                     # (show-dns, woken by set_senders)
        rx.tick(10.1)                                         # a new list: subscribed at once, not at 11.0
        assert _subs(a) == [["room-mic"]] and _subs(b) == [["test-audio"]]
        rx.tick(10.6)
        h = rx.health()["senders"]
        assert set(h) == {"one", "two"} and h["two"]["on_demand"] is True and h["two"]["hosts"][0]["host"] == "127.0.0.1"
        rx.set_senders((two,))                                # "one" removed: no more SUBSCRIBEs to it
        rx._resolve_all()
        rx.tick(11.7)
        rx.tick(12.8)
        assert _subs(a) == [] and len(_subs(b)) == 2
        assert set(rx.health()["senders"]) == {"two"}
    finally:
        for x in (a, b, rsock):
            x.close()


def test_host_fallback_subscribes_all_then_the_host_that_answers():
    first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    first.bind(("127.0.0.2", 0))                              # loopback, another address (Linux: all of 127/8)
    port = first.getsockname()[1]
    second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    second.bind(("127.0.0.1", port))
    rsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rsock.bind(("127.0.0.1", 0))
    try:
        s = Sender("kiosk", "127.0.0.2", port, ("room-mic",), hosts=("127.0.0.2", "127.0.0.1"))
        rx, rec = make(Config(senders=(s,)), sock=rsock)
        rx.set_off_streams(())                                # the mic is on: nothing off
        rx._resolve_all()
        rx.tick(20.0)                                         # nobody has answered: every host
        assert _subs(first) == [["room-mic"]] and _subs(second) == [["room-mic"]]
        h = rx.health()["senders"]["kiosk"]
        assert h["using"] is None and [r["host"] for r in h["hosts"]] == ["127.0.0.2", "127.0.0.1"]
        rx.handle(apkt(0), ("127.0.0.1", port), 20.5)          # the second host answers (Wi-Fi, say)
        rx.tick(21.0)
        assert _subs(first) == [] and _subs(second) == [["room-mic"]]
        rx.tick(21.5)
        h = rx.health()["senders"]["kiosk"]
        assert h["using"] == "127.0.0.1" and h["host"] == "127.0.0.1" and h["addr"] == f"127.0.0.1:{port}"
        assert h["hosts"][1]["answered_age_ms"] == 1000.0 and h["hosts"][0]["answered_age_ms"] is None
        rx.handle(apkt(1), ("127.0.0.2", port), 21.6)          # the first host answers too: it is first in the list
        rx.tick(22.0)
        assert _subs(first) == [["room-mic"]] and _subs(second) == []
        rx.tick(26.0)                                         # silence for > 3 s: every host again
        assert _subs(first) == [["room-mic"]] and _subs(second) == [["room-mic"]]
        rx.handle(apkt(2), ("127.0.0.1", port + 1 if port < 65535 else port - 1), 26.5)   # another port: no answer
        rx.tick(27.0)
        assert len(_subs(first)) == 1 and len(_subs(second)) == 1
    finally:
        for x in (first, second, rsock):
            x.close()


def test_set_priority_lifts_a_test_stream_after_the_switch_up_time():
    rx, rec = make(Config(stream_priority=("pc22-loop",)))
    mic = Sim(rx, "room-mic")
    test = Sim(rx, "test-audio", addr=("127.0.0.1", 6999), t0=T0 + 0.25)
    mic.heartbeats = test.heartbeats = False
    end = T0 + 2.0
    while mic.arrival(mic.i) < end:
        first = mic if mic.arrival(mic.i) <= test.arrival(test.i) else test
        first.run(1)
    assert rx.active_audio() == "room-mic"
    assert rx.health()["streams"]["test-audio"]["rank"] == contract.stream_rank("test-audio") > 0
    rx.set_priority(("test-audio",))
    t_set = mic.arrival(mic.i)
    end = T0 + 8.0
    while mic.arrival(mic.i) < end:
        first = mic if mic.arrival(mic.i) <= test.arrival(test.i) else test
        first.run(1)
    assert rx.active_audio() == "test-audio"
    blocks = rec.of("test-audio")
    assert blocks and blocks[0][5] & AUDIO_SEGMENT                    # streaming 3 s by then: the usual rule
    assert 0.0 <= blocks[0][2] - t_set <= 1.6, blocks[0][2] - t_set
    h = rx.health()
    assert h["priority"] == ["test-audio", "pc22-loop"] and h["streams"]["test-audio"]["rank"] < 0
    rx.set_priority(())                                       # the test stops: room-mic outranks it again
    end = T0 + 13.0
    while mic.arrival(mic.i) < end:
        first = mic if mic.arrival(mic.i) <= test.arrival(test.i) else test
        first.run(1)
    assert rx.active_audio() == "room-mic" and rx.health()["priority"] == ["pc22-loop"]


def test_end_to_end_over_localhost_udp(sender_sock):
    """A real receiver thread and an in-process VBAN sender: SUBSCRIBE handshake, audio with one packet lost,
    a heartbeat, MIDI and an unknown stream, all on ephemeral 127.0.0.1 ports."""
    port = sender_sock.getsockname()[1]
    cfg = Config(senders=(Sender("kiosk", "localhost", port, ("room-mic", "room-piano")),),
                 vban_bind=("127.0.0.1", 0))
    rx, rec = make(cfg)
    rx.set_off_streams(())                                      # the mic is on (README S5.16)
    rx.start()
    try:
        sender_sock.settimeout(3.0)
        data, back = sender_sock.recvfrom(2048)                 # the SUBSCRIBE handshake
        assert vban.subscribed_streams(vban.parse(data)) == ["room-mic", "room-piano"]
        t_start = time.perf_counter()
        for c in range(80):
            if c != 40:                                         # one packet lost on the way
                sender_sock.sendto(apkt(c), back)
            if c % 20 == 19:
                sender_sock.sendto(hbpkt("room-mic", c // 20, sess="e2e", frame=c, n=c * FR,
                                         t_cap_ns=time.monotonic_ns(), dev="loop"), back)
            delay = t_start + (c + 1) * PKT_S - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        sender_sock.sendto(apkt(0, "mystery"), back)
        sender_sock.sendto(midi_batch(1, time.monotonic_ns(), 0), back)
        sender_sock.sendto(b"junk", back)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            h = rx.health()
            if (len(rec.of("room-mic")) >= 80 and rec.midi and "mystery" in h.get("streams", {})
                    and h["socket"]["junk"] >= 1):
                break
            time.sleep(0.05)
        mic = rec.of("room-mic")
        assert [b[1] for b in mic] == [i * FR for i in range(80)]
        flags = [b[5] for b in mic]
        assert flags[0] == AUDIO_SEGMENT and flags[40] == AUDIO_GAP and flags[41] == AUDIO_RESUME
        assert not mic[40][3].any() and all(math.isfinite(b[2]) for b in mic)
        assert rec.midi[0][0] == "room-piano" and math.isfinite(rec.midi[0][3](rec.midi[0][1].ev[0][0]))
        h = rx.health()
        assert h["streams"]["mystery"]["rank"] == contract.UNKNOWN_RANK
        assert h["streams"]["room-mic"]["sender"]["sess"] == "e2e"
        assert h["streams"]["room-mic"]["net"]["gaps"] == 1 and h["socket"]["junk"] >= 1
        assert h["socket"]["bind"].startswith("127.0.0.1:") and h["senders"]["kiosk"]["addr"] == f"127.0.0.1:{port}"
        assert {t.name for t in threading.enumerate()} >= {"show-rx", "show-dns"}
        ticks = np.diff(rec.ticks[-50:])
        assert len(rec.ticks) > 20 and np.median(ticks) < 0.02
    finally:
        rx.stop()
    assert not any(t.name == "show-rx" and t.is_alive() for t in threading.enumerate())


def test_bind_failure_is_reported_and_retried(sender_sock):
    taken = sender_sock.getsockname()
    rx, rec = make(Config(vban_bind=taken))
    rx.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not rx.health()["socket"]["error"]:
            time.sleep(0.02)
        assert rx.health()["socket"]["error"]
        assert rec.ticks                                        # ticks go on without a socket
    finally:
        rx.stop()


class _FlakySock:
    """A real bound socket whose first ``fails`` receives raise (an ICMP refusal, say), then work."""

    def __init__(self, sock: socket.socket, fails: int = 1) -> None:
        self._s, self.fails = sock, fails

    def __getattr__(self, name):
        return getattr(self._s, name)

    def recvfrom_into(self, buf):
        if self.fails:
            self.fails -= 1
            raise ConnectionRefusedError(111, "Connection refused")
        return self._s.recvfrom_into(buf)


def test_a_transient_socket_error_clears_on_the_next_packet(sender_sock):
    rsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rsock.bind(("127.0.0.1", 0))
    rx, rec = make(Config(), sock=_FlakySock(rsock))
    rx.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not rx.health()["socket"]["error"]:
            time.sleep(0.02)
        assert "refused" in rx.health()["socket"]["error"]
        sender_sock.sendto(apkt(0), rsock.getsockname())
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and (rx.health()["socket"]["error"] or not rec.of("room-mic")):
            time.sleep(0.02)
        assert rx.health()["socket"]["error"] is None and rec.of("room-mic")
    finally:
        rx.stop()
        rsock.close()


# -- the Room page's Mic choice (inputs.py) ---------------------------------------------------------------------------
def test_set_choice_ranks_after_a_test_stream_and_before_the_config():
    rx, rec = make(Config(stream_priority=("pc22-loop",)))
    rx.set_choice("pc22-mic")
    rx.tick(10.0)
    assert rx._priority == ("pc22-mic", "pc22-loop")
    rx.set_priority(("test-audio",))                          # a test plays: it still comes first
    rx.tick(10.1)
    assert rx._priority == ("test-audio", "pc22-mic", "pc22-loop")
    rx.set_priority(())                                       # the test stops: the choice stays
    rx.tick(10.2)
    assert rx._priority == ("pc22-mic", "pc22-loop")
    rx.set_choice(None)                                       # automatic
    rx.tick(10.3)
    assert rx._priority == ("pc22-loop",)
    rx.tick(10.6)
    assert rx.health()["choice"] == []


def test_device_hints_ride_each_senders_subscribe_and_go_at_once():
    a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for x in (a, rsock):
        x.bind(("127.0.0.1", 0))
    try:
        rx, rec = make(Config(senders=(Sender("pc22", "localhost", a.getsockname()[1], ("pc22-mic",)),)), sock=rsock)
        rx._resolve_all()
        rx.tick(10.0)
        a.settimeout(0.5)
        assert vban.subscribed_devices(vban.parse(a.recvfrom(2048)[0])) == {}
        rx.set_device_hints({"pc22-mic": "Dell", "room-mic": "elsewhere"})
        rx.tick(10.2)                                         # sent at once, not at 11.0
        assert vban.subscribed_devices(vban.parse(a.recvfrom(2048)[0])) == {"pc22-mic": "Dell"}
        rx.tick(10.6)
        assert rx.health()["device_hints"] == {"pc22-mic": "Dell", "room-mic": "elsewhere"}
    finally:
        for x in (a, rsock):
            x.close()


# -- the mic on demand (README S5.16) ----------------------------------------------------------------------------------
def _sub_objs(sock: socket.socket, wait: float = 0.05) -> list[tuple[list[str], list[str]]]:
    """Every SUBSCRIBE waiting on ``sock`` as (subscribe, off)."""
    out = []
    sock.settimeout(wait)
    while True:
        try:
            data, _ = sock.recvfrom(2048)
        except (socket.timeout, BlockingIOError):
            return out
        pkt = vban.parse(data)
        out.append((vban.subscribed_streams(pkt), vban.subscribed_off(pkt)))


def test_privacy_at_start_holds_every_mic_off_until_a_decision_covers_it():
    k = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    p = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for x in (k, p, t, rsock):
        x.bind(("127.0.0.1", 0))
    try:
        kiosk = Sender("kiosk", "127.0.0.1", k.getsockname()[1], ("room-mic", "room-piano"))
        pc22 = Sender("pc22", "127.0.0.1", p.getsockname()[1], ("pc22-mic",))
        test = Sender("test-audio", "127.0.0.1", t.getsockname()[1], ("test-audio",), on_demand=True)
        rx, rec = make(Config(senders=(kiosk, pc22, test)), sock=rsock)
        assert rx.off_streams() == {"room-mic", "pc22-mic"}              # no decision yet: every possible mic held
        rx._resolve_all()
        rx.tick(10.0)
        assert _sub_objs(k) == [(["room-piano"], ["room-mic"])]        # MIDI at once, the mic kept closed
        assert _sub_objs(p) == [([], ["pc22-mic"])]                    # an empty subscribe keeps its off heartbeats
        assert _sub_objs(t) == [(["test-audio"], [])]                  # test streams are never the mic
        rx.tick(10.6)
        h = rx.health()
        assert h["off"] == ["pc22-mic", "room-mic"] and h["off_held"] == ["pc22-mic", "room-mic"]
        assert h["off_decided"] is False
        rx.set_off_streams(["pc22-mic"])                               # the decision: pc22's mic off, the kiosk's on
        rx.tick(10.7)                                                  # sent at once, not at 11.0
        assert _sub_objs(k) == [(["room-mic", "room-piano"], [])] and _sub_objs(p) == [([], ["pc22-mic"])]
        rx.tick(11.2)
        h = rx.health()
        assert h["off"] == ["pc22-mic"] and h["off_held"] == [] and h["off_decided"] is True
        # a sender added later: its mic waits for a decision that covers it
        new = Sender("loft", "127.0.0.1", k.getsockname()[1] + 0, ("loft-mic",))
        rx.set_senders((kiosk, pc22, test, new))
        rx._resolve_all()
        rx.tick(11.3)
        assert "loft-mic" in rx.off_streams()
        rx.set_off_streams([], covered=["room-mic", "room-piano", "pc22-mic", "test-audio", "loft-mic"])
        rx.tick(11.4)
        assert rx.off_streams() == frozenset()
    finally:
        for x in (k, p, t, rsock):
            x.close()


def test_audio_of_a_stream_kept_off_is_dropped_and_the_active_stream_leaves_at_once():
    levels: list = []
    rx, rec = make(on_level=lambda *a, **kw: levels.append(a[0]))
    mic = Sim(rx, "room-mic")
    pc = Sim(rx, "pc22-mic", addr=("192.0.2.11", 40001), t0=T0 + 0.001)
    end = T0 + 5.0
    while mic.arrival(mic.i) < end:
        first = mic if mic.arrival(mic.i) <= pc.arrival(pc.i) else pc
        first.run(1)
    assert rx.active_audio() == "room-mic" and levels and set(levels) == {"room-mic"}
    n_mic, n_lv = len(rec.of("room-mic")), len(levels)
    rx.set_off_streams(["room-mic"])
    t_off = mic.arrival(mic.i)
    end = t_off + 3.0
    while mic.arrival(mic.i) < end:                                    # a sender that ignores the off
        first = mic if mic.arrival(mic.i) <= pc.arrival(pc.i) else pc
        first.run(1)
    assert len(rec.of("room-mic")) <= n_mic + 1                        # (the tick that applies it: at most one)
    assert set(levels[n_lv + 1:]) <= {"pc22-mic"}
    assert rx.active_audio() == "pc22-mic"                             # left at once, no 1 s wait
    first_pc = next(b for b in rec.of("pc22-mic"))
    assert first_pc[5] & AUDIO_SEGMENT and first_pc[2] - t_off < 0.2
    rx.tick(end + 0.01)
    rx.tick(end + 0.6)
    h = rx.health()
    st = h["streams"]["room-mic"]
    assert st["state"] == "off" and h["off"] == ["room-mic"] and h["active_audio"] == "pc22-mic"
    assert st["off_dropped"] > 1000 and 0 < st["off_ignored"] < st["off_dropped"]     # past the 1 s grace: ignored
    assert h["off_dropped"] == st["off_dropped"] and h["off_ignored"] == st["off_ignored"]
    rx.set_off_streams([])                                             # back on: an idle segment, then live again
    n_mic = len(rec.of("room-mic"))
    pc.heartbeats = False
    t_on = mic.arrival(mic.i)
    end = t_on + 6.0
    while mic.arrival(mic.i) < end:
        first = mic if mic.arrival(mic.i) <= pc.arrival(pc.i) else pc
        first.run(1)
    assert rx.health()["streams"]["room-mic"]["state"] == "streaming" and rx.active_audio() == "room-mic"
    assert rec.of("room-mic")[n_mic][5] & AUDIO_SEGMENT


def test_a_stream_back_from_off_needs_5_s_of_new_silence_to_read_quiet():
    rx, rec = make()
    mic = Sim(rx, "room-mic", v=0.0)                                  # silence
    mic.heartbeats = False
    mic.seconds(7.0)
    rx.tick(mic.arrival(mic.i))
    assert rx.health()["streams"]["room-mic"]["state"] == "quiet"
    rx.set_off_streams(["room-mic"])
    rx.tick(mic.arrival(mic.i) + 0.01)
    rx.set_off_streams([])
    mic.t0 += 3.0
    t = mic.seconds(1.0)
    rx.tick(t + 0.2)
    assert rx.health()["streams"]["room-mic"]["state"] == "streaming"    # not "quiet" from before the off
    t = mic.seconds(5.5)
    rx.tick(t + 0.2)
    assert rx.health()["streams"]["room-mic"]["state"] == "quiet"


def test_an_off_heartbeat_reads_off_until_audio_comes_again():
    rx, rec = make()
    mic = Sim(rx, "room-mic")
    mic.seconds(2.0)
    assert rx.active_audio() == "room-mic"
    t = mic.arrival(mic.i)
    rx.handle(hbpkt("room-mic", 900, sess="A", state="off", devices=["ATR4697"]), ADDR, t)   # the sender closed it
    rx.tick(t + 0.2)
    h = rx.health()
    assert h["streams"]["room-mic"]["state"] == "off" and rx.active_audio() is None
    assert h["streams"]["room-mic"]["sender"]["devices"] == ["ATR4697"]
    rx.tick(t + 1.0)
    assert rx.health()["streams"]["room-mic"]["state"] == "off"         # fresh heartbeats keep it off, never "down"
    mic.t0 += 1.5                                                     # it opens again: audio after that heartbeat
    mic.heartbeats = False
    mic.seconds(4.0)
    assert rx.health()["streams"]["room-mic"]["state"] == "streaming" and rx.active_audio() == "room-mic"
