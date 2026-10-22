"""piano_render/sonos.py against fakes only: a fake room_service (GET /api/v1/status, PUT /api/v1/room/targets, its
Sonos adapter's unknown-key 400) and a fake Sonos UPnP responder (read-only GetTransportInfo / GetPositionInfo; any
other action fails the test). Nothing reaches the real room_service or a real Sonos; files in tmp only.

    chrt -i 0 nice -n 19 ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests/test_sonos.py
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sf = pytest.importorskip("soundfile")
from piano_render import sonos  # noqa: E402

KEY = "0123456789abcdef0123456789abcdef"
SR = 48000
ZONE = "drawing-room"
TARGET = f"sonos.{ZONE}.playback"


# -- fakes ----------------------------------------------------------------------------------------------------------
class FakeSonos:
    """The player's transport as UPnP reads it; plays what the fake room tells it to, after ``delay`` s."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state, self.uri, self.t0, self.dur = "STOPPED", "", None, 0.0
        self.delay = 0.3                       # PUT -> playing (its own buffer fill)
        self.rel_offset = 0.0                  # RelTime ahead of the truth by this much
        self.actions: list[str] = []
        self.reads_fail = False

    def play(self, uri: str, dur: float) -> None:
        def go():
            time.sleep(self.delay)
            with self.lock:
                self.state, self.uri, self.t0, self.dur = "PLAYING", uri, time.monotonic(), dur
        threading.Thread(target=go, daemon=True).start()

    def set(self, state: str, uri: "str | None" = None) -> None:
        with self.lock:
            self.state = state
            if uri is not None:
                self.uri = uri

    def read(self) -> tuple[str, str, str]:
        with self.lock:
            rel = 0.0
            if self.t0 is not None:
                rel = time.monotonic() - self.t0 + self.rel_offset
                if self.state == "PLAYING" and rel >= self.dur:
                    self.state, rel = "STOPPED", 0.0
            s = max(0, int(math.floor(rel))) if self.state == "PLAYING" else 0
            return self.state, self.uri, f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _serve(handler_cls) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def upnp_server(dev: FakeSonos) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            action = self.headers.get("SOAPACTION", "").strip('"').split("#")[-1]
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            dev.actions.append(action)
            assert action in ("GetTransportInfo", "GetPositionInfo"), action      # read-only, never a command
            if dev.reads_fail:
                self.send_response(500)
                self.end_headers()
                return
            state, uri, rel = dev.read()
            fields = ({"CurrentTransportState": state, "CurrentTransportStatus": "OK", "CurrentSpeed": "1"}
                      if action == "GetTransportInfo" else
                      {"Track": "1", "TrackDuration": "0:00:10", "TrackMetaData": "", "TrackURI": uri, "RelTime": rel,
                       "AbsTime": "NOT_IMPLEMENTED", "RelCount": "2147483647", "AbsCount": "2147483647"})
            inner = "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
            body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<u:{action}Response xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">{inner}'
                    f'</u:{action}Response></s:Body></s:Envelope>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return _serve(H)


class FakeRoom:
    """room_service's two calls. ``takes``: the playback keys its adapter accepts (the real one's EXTRAS, plus what ff
    adds); a PUT naming others gets the real adapter's 400."""

    def __init__(self, dev: FakeSonos) -> None:
        self.dev = dev
        self.takes = {"uri", "shuffle", "volume", "only_if_idle", "fade_s", "title"}
        self.volume, self.state, self.track = 40, "stopped", ""
        self.stored: dict = {}
        self.puts: list[tuple[float, dict]] = []
        self.play_s = 3.0                    # how long a play plays (the fake Sonos stops itself after it)

    def status(self) -> dict:
        state, uri, _ = self.dev.read()
        return {"subsystems": {"sonos": {"available": True, "believed": {
                    f"{ZONE}.volume": self.volume, f"{ZONE}.playback": "stopped",
                    f"{ZONE}.now_playing": {"state": self.state or state.lower(), "track_id": self.track or uri}}}},
                "stored": self.stored}

    def put(self, body: dict) -> tuple[int, dict]:
        self.puts.append((time.monotonic(), body))
        (t, v), = body["values"].items()
        if t != TARGET:
            return 404, {"error": "unknown_target", "detail": t}
        if v == "paused":
            self.dev.set("PAUSED_PLAYBACK")
            return 202, {"accepted": [t]}
        extra = sorted(set(v) - self.takes)
        if extra:
            return 400, {"error": "bad_value", "detail": f"{t}: playback has unknown keys {extra}; it takes "
                                                         f"{sorted(self.takes - {'uri'})}"}
        self.dev.play(v["uri"], self.play_s)
        return 202, {"accepted": [t]}


def room_server(room: FakeRoom) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status: int, doc: dict) -> None:
            body = json.dumps(doc).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            assert self.path == "/api/v1/status", self.path
            self._send(200, room.status())

        def do_PUT(self):
            assert self.path == "/api/v1/room/targets", self.path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self._send(*room.put(body))
    return _serve(H)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """(out factory, FakeRoom, FakeSonos, events, render path); sonos.UNSUPPORTED cleared; fast watch."""
    monkeypatch.setattr(sonos, "UNSUPPORTED", set())
    monkeypatch.setattr(sonos, "WATCH_HZ", 10.0)
    monkeypatch.setattr(sonos, "END_SLACK_S", 0.4)
    dev = FakeSonos()
    room = FakeRoom(dev)
    servers = [upnp_server(dev), room_server(room)]
    cache = tmp_path / "renders"
    cache.mkdir()
    render = cache / f"{KEY}.flac"
    t = np.arange(int(1.0 * SR)) / SR
    x = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(render), np.stack([x, -x], 1), SR, subtype="PCM_24")
    (tmp_path / "sonos.json").write_text(json.dumps({"zones": {ZONE: {"room": "Drawing Room", "ip": "127.0.0.1"}}}))
    (tmp_path / "latency.json").write_text(json.dumps({"outputs": {"tv-south": {}}}))
    events: list[dict] = []

    def make(**kw):
        return sonos.SonosOutput(ZONE, emit=events.append, room_url=f"http://127.0.0.1:{servers[1].server_port}",
                                 sonos_file=tmp_path / "sonos.json", latency_file=tmp_path / "latency.json",
                                 upnp_port=servers[0].server_port, **kw)
    yield make, room, dev, events, render, tmp_path
    for s in servers:
        s.shutdown()


def url_of(key: str = KEY) -> str:
    return f"http://lan-host:8700/show/api/v1/piano/audio/{key}.flac"


# -- the pure parts -----------------------------------------------------------------------------------------------
def test_reltime_uri_keys_and_start_ms(tmp_path):
    assert sonos.rel_s("0:03:54") == 234 and sonos.rel_s("1:00:01") == 3601 and sonos.rel_s("NOT_IMPLEMENTED") is None
    assert sonos.same_uri("http://h/a%20b.flac", "http://h/a b.flac") and not sonos.same_uri("", "")
    assert sonos.unknown_keys("x: playback has unknown keys ['fade_s', 'title']; it takes ['volume']") == \
        {"fade_s", "title"}
    assert sonos.unknown_keys("x: playback has unknown keys ['title']; it takes ['fade_s', 'volume']") == {"title"}
    assert sonos.unknown_keys("volume is 0..100") == set()
    lat = tmp_path / "latency.json"
    assert sonos.start_ms_for(ZONE, lat) == sonos.START_MS_DEFAULT == 500.0          # no file
    lat.write_text(json.dumps({"outputs": {"sonos:drawing-room": {"start_ms": 620}}}))
    assert sonos.start_ms_for(ZONE, lat) == 620.0
    lat.write_text(json.dumps({"outputs": {"sonos:drawing-room": {"start_ms": "soon"}}}))
    assert sonos.start_ms_for(ZONE, lat) == 500.0


def test_padded_copy_is_silence_then_the_render_and_is_reused(tmp_path):
    src = tmp_path / f"{KEY}.flac"
    x = (np.random.default_rng(1).uniform(-0.5, 0.5, (SR // 2, 2))).astype(np.float32)
    sf.write(str(src), x, SR, subtype="PCM_24")
    out = sonos.padded_copy(src, 0.75)
    assert out.parent == tmp_path / "sonos" and sonos.KEY_RE.match(out.stem) and out.stem != KEY
    a, sr = sf.read(str(src), dtype="int32")
    b, sr2 = sf.read(str(out), dtype="int32")
    n = int(0.75 * SR)
    assert sr2 == sr and sf.info(str(out)).subtype == "PCM_24" and b.shape == (n + len(a), 2)
    assert not b[:n].any() and np.array_equal(b[n:], a)                     # bit for bit after the silence
    t = out.stat().st_mtime_ns
    time.sleep(0.01)
    assert sonos.padded_copy(src, 0.75) == out and out.stat().st_mtime_ns >= t     # reused (touched), not rewritten
    assert sonos.padded_copy(src, 4.5) != out
    for i in range(4):
        sonos.padded_copy(src, 1.0 + i, keep=3)
    assert len(list((tmp_path / "sonos").glob("*.flac"))) == 3 and not list((tmp_path / "sonos").glob(".*tmp"))


# -- volume ---------------------------------------------------------------------------------------------------------
def test_the_volume_is_the_current_one_else_the_stored_one_else_0_with_a_warning(rig):
    make, room, dev, events, render, _ = rig
    out = make()
    assert out.choose_volume(None, out.room_state()) == 40 and not events
    assert out.choose_volume(25, out.room_state()) == 25                          # asked: as asked
    room.volume = 0
    room.stored = {"other": {f"sonos.{ZONE}.volume": {"value": 22}},
                   "_restore": {f"sonos.{ZONE}.volume": {"value": 35}}}              # Piano mode put it to 0
    assert out.room_state()["put_back"] == 35 and out.choose_volume(None, out.room_state()) == 35
    room.stored = {}
    assert out.choose_volume(None, out.room_state()) == 0
    assert events == [{"ev": "warning", "why": "Sonos volume is 0"}]
    room.volume = None
    assert out.choose_volume(None, out.room_state()) is None and "isn't known" in events[-1]["why"]
    with pytest.raises(sonos.SonosError):
        out.choose_volume(150, out.room_state())


def test_a_play_carries_the_volume_title_and_no_fade(rig):
    make, room, dev, events, render, _ = rig
    room.volume = 0
    room.stored = {"_restore": {f"sonos.{ZONE}.volume": {"value": 40}}}
    out = make()
    t_audio0 = out.start(render, lead_in_ms=750, url=url_of(), title="Piano: Nocturne")
    (t_put, body), = room.puts
    v = body["values"][TARGET]
    assert body["source"] == "test piano" and v["volume"] == 40 and v["fade_s"] == 0 and v["title"] == "Piano: Nocturne"
    padded = sonos.padded_name(KEY, 0.75)
    assert v["uri"] == url_of(padded) and (render.parent / "sonos" / f"{padded}.flac").exists()
    assert out.pad_s == 0.75 and t_audio0 == pytest.approx(t_put + 0.5 + 0.75, abs=0.05)
    out.stop()


# -- feature detection ----------------------------------------------------------------------------------------------
def test_unknown_keys_are_dropped_for_the_process_and_the_fade_is_padded(rig, tmp_path):
    make, room, dev, events, render, d = rig
    room.takes -= {"fade_s", "title"}                         # today's room_service
    (d / "latency.json").write_text(json.dumps({"outputs": {"sonos:drawing-room": {"start_ms": 620}}}))
    out = make()
    t_audio0 = out.start(render, lead_in_ms=750, url=url_of(), title="Piano: Nocturne")
    assert [sorted(b["values"][TARGET]) for _, b in room.puts] == [["fade_s", "title", "uri", "volume"],
                                                                    ["uri", "volume"]]
    assert sonos.UNSUPPORTED == {"fade_s", "title"}
    pad = sonos.RAMP_S_DEFAULT + sonos.FADE_PAD_EXTRA_S                       # 4.5: the 4 s fade-in hears silence
    assert out.pad_s == pad and room.puts[1][1]["values"][TARGET]["uri"] == url_of(sonos.padded_name(KEY, pad))
    assert t_audio0 == pytest.approx(room.puts[1][0] + 0.62 + pad, abs=0.05)      # not playing before: no fade-out
    out.stop()
    room.puts.clear()
    out2 = make()                                             # the same process: no second try
    out2.start(render, lead_in_ms=750, url=url_of(), title="Piano: Nocturne")
    assert len(room.puts) == 1 and sorted(room.puts[0][1]["values"][TARGET]) == ["uri", "volume"]
    out2.stop()


def test_only_the_named_key_is_dropped_and_another_refusal_is_an_error(rig):
    make, room, dev, events, render, d = rig
    room.takes -= {"title"}                                   # fade_s shipped first
    out = make()
    out.start(render, lead_in_ms=750, url=url_of(), title="Piano: X")
    assert sonos.UNSUPPORTED == {"title"} and out.pad_s == 0.75
    assert sorted(room.puts[-1][1]["values"][TARGET]) == ["fade_s", "uri", "volume"]
    out.stop()
    room.takes -= {"volume"}
    with pytest.raises(sonos.SonosError, match="refused"):
        make().start(render, lead_in_ms=750, url=url_of())


def test_with_music_playing_and_no_fade_s_the_fade_out_is_added(rig):
    make, room, dev, events, render, d = rig
    room.takes -= {"fade_s"}
    (d / "sonos.json").write_text(json.dumps({"ramp_s": 3.0, "zones": {ZONE: {"room": "Drawing Room",
                                                                             "ip": "127.0.0.1"}}}))
    dev.set("PLAYING", "x-sonosapi-stream:radio")              # someone's radio
    out = make()
    t_audio0 = out.start(render, lead_in_ms=750, url=url_of())
    assert out.pad_s == 3.5 and t_audio0 == pytest.approx(room.puts[-1][0] + 3.0 + 0.5 + 3.5, abs=0.05)
    out.stop()


# -- the watch ------------------------------------------------------------------------------------------------------
def test_played_to_the_end(rig):
    make, room, dev, events, render, _ = rig
    room.play_s = 0.75 + 1.0                                  # the padded file's length
    out = make()
    out.start(render, lead_in_ms=750, url=url_of())
    assert out.wait(6.0)
    assert out.session.error is None and out.session.done["by"] == "sonos"
    out.stop()
    assert [b["values"][TARGET] for _, b in room.puts][1:] == []                  # ended by itself: no pause


@pytest.mark.parametrize("what, why", [("STOPPED", "Sonos stopped"), ("PAUSED_PLAYBACK", "Sonos paused"),
                                       ("other", "Sonos is playing something else")])
def test_leaving_our_file_early_is_an_error(rig, what, why):
    make, room, dev, events, render, _ = rig
    room.play_s = 30.0
    out = make()
    out.start(render, lead_in_ms=750, url=url_of())
    time.sleep(0.8)
    dev.set("PLAYING", "x-sonos-spotify:other") if what == "other" else dev.set(what)
    assert out.wait(3.0) and out.session.error == why and out.session.done is None


def test_not_starting_is_an_error(rig, monkeypatch):
    make, room, dev, events, render, _ = rig
    monkeypatch.setattr(sonos, "START_TIMEOUT_S", 0.6)
    dev.delay = 5.0
    out = make()
    out.start(render, lead_in_ms=750, url=url_of())
    assert out.wait(3.0) and out.session.error == "Sonos didn't start"


def test_a_late_start_is_rebased_from_the_second_edges(rig):
    make, room, dev, events, render, _ = rig
    room.play_s = 30.0
    dev.delay = 1.6                                           # 1.1 s later than the 0.5 s estimate
    out = make()
    t_put0 = time.monotonic()
    est = out.start(render, lead_in_ms=750, url=url_of())
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not any(e["ev"] == "rebase" for e in events):
        time.sleep(0.05)
    reb = [e for e in events if e["ev"] == "rebase"]
    assert len(reb) == 1 and reb[0]["was"] == est
    truth = dev.t0 + 0.75                                     # MIDI time 0: the file's start plus the pad
    assert abs(reb[0]["t_audio0"] - truth) < 0.25 and abs(est - truth) > 0.9 and t_put0 < dev.t0
    assert out.t_audio0 == reb[0]["t_audio0"]
    out.stop()


def test_on_time_is_not_rebased(rig):
    make, room, dev, events, render, _ = rig
    room.play_s = 30.0
    dev.delay = 0.45
    out = make()
    out.start(render, lead_in_ms=750, url=url_of())
    time.sleep(3.6)                                           # three second edges
    assert not [e for e in events if e["ev"] == "rebase"]
    out.stop()


def test_an_unreadable_sonos_is_timed_by_the_clock(rig, monkeypatch):
    make, room, dev, events, render, _ = rig
    monkeypatch.setattr(sonos, "UNREADABLE_S", 0.3)
    out = make()
    t_audio0 = out.start(render, lead_in_ms=750, url=url_of())
    dev.reads_fail = True
    assert out.wait(6.0) and out.session.done["by"] == "clock"
    assert time.monotonic() >= t_audio0 - 0.75 + 1.75 + sonos.END_SLACK_S - 0.05
    assert any("timed by the clock" in e.get("why", "") for e in events)


# -- stop -----------------------------------------------------------------------------------------------------------
def test_stop_pauses_only_our_file(rig):
    make, room, dev, events, render, _ = rig
    room.play_s = 30.0
    out = make()
    out.start(render, lead_in_ms=750, url=url_of())
    time.sleep(0.6)
    out.stop()
    assert room.puts[-1][1]["values"] == {TARGET: "paused"} and dev.state == "PAUSED_PLAYBACK"
    n = len(room.puts)
    out.stop()                                                # again: nothing of ours plays
    assert len(room.puts) == n
    dev.set("PLAYING")                                        # room_service's fade-out still plays our file
    out.stop()
    assert len(room.puts) == n                                # one pause per play: never a second one mid-fade
    out2 = make()
    out2.start(render, lead_in_ms=750, url=url_of())
    time.sleep(0.6)
    dev.set("PLAYING", "x-sonosapi-stream:radio")             # someone changed it in the Sonos app
    time.sleep(0.3)
    n = len(room.puts)
    out2.stop()
    assert len(room.puts) == n and dev.uri == "x-sonosapi-stream:radio"


def test_a_stop_before_our_file_is_heard_pauses_it_when_it_is(rig):
    make, room, dev, events, render, _ = rig
    room.play_s = 30.0
    dev.delay = 1.0
    out = make()
    out.start(render, lead_in_ms=750, url=url_of())
    out.stop()                                                # still the old state: nothing to pause yet
    assert room.puts[-1][1]["values"][TARGET] != "paused"
    out.settle(4.0)
    assert room.puts[-1][1]["values"] == {TARGET: "paused"} and dev.state == "PAUSED_PLAYBACK"


def test_a_stop_during_the_start_stops_it_before_the_put(rig, monkeypatch):
    make, room, dev, events, render, _ = rig
    out = make()
    real = sonos.padded_copy

    def slow(src, pad_s, **kw):
        out.stop()                                            # the stop lands while the copy is being made
        return real(src, pad_s, **kw)
    monkeypatch.setattr(sonos, "padded_copy", slow)
    with pytest.raises(sonos.SonosError, match="stopped before"):
        out.start(render, lead_in_ms=750, url=url_of())
    assert room.puts == []


def test_an_unknown_zone_and_a_url_that_isnt_the_render(rig, tmp_path):
    make, room, dev, events, render, d = rig
    with pytest.raises(sonos.SonosError, match="no Sonos zone"):
        sonos.SonosOutput("kitchen", sonos_file=d / "sonos.json")
    out = make()
    out.start(render, lead_in_ms=750, url="http://lan-host:8700/elsewhere.flac")
    assert out.pad_s == 0.0 and room.puts[-1][1]["values"][TARGET]["uri"] == "http://lan-host:8700/elsewhere.flac"
    out.stop()
    assert set(dev.actions) <= {"GetTransportInfo", "GetPositionInfo"}
