"""piano_render/sonos.py against fakes only: a fake room_service (GET /api/v1/status, PUT /api/v1/room/targets, its
Sonos adapter's unknown-key 400), a fake Sonos UPnP responder (read-only GetTransportInfo / GetPositionInfo /
GetMediaInfo; any other action fails the test) that fetches a radio stream over HTTP like the real one, and a fake
show_service stream route (follows the growing MP3 to its .done). Nothing reaches the real room_service or a real
Sonos; files in tmp only (streams: real ffmpeg, into tmp).

    chrt -i 0 nice -n 19 ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests/test_sonos.py
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
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
        self.media_uri = ""                    # GetMediaInfo's CurrentURI
        self.track_override = None             # a radio stream's TrackURI, when the Sonos reports another one
        self.stop_at_end = True                # a stream: STOPPED once what it fetched has played (else PLAYING on)
        self.fetched = b""
        self.fetch_done = threading.Event()
        self.gets: list[str] = []

    def play(self, uri: str, dur: float) -> None:
        def go():
            time.sleep(self.delay)
            with self.lock:
                self.state, self.uri, self.media_uri, self.t0, self.dur = "PLAYING", uri, uri, time.monotonic(), dur
        threading.Thread(target=go, daemon=True).start()

    def play_stream(self, uri: str) -> None:
        """A radio URI: fetch it over plain http (as a Sonos does), play from its first byte, to its end."""
        def go():
            time.sleep(self.delay)
            url = "http://" + uri.split("://", 1)[1]
            self.gets.append(url)
            r = urllib.request.urlopen(url, timeout=10)
            with self.lock:
                self.state, self.uri, self.media_uri = "PLAYING", uri, uri
                self.t0, self.dur = time.monotonic(), float("inf")
            buf = bytearray()
            while True:
                b = r.read(4096)
                if not b:
                    break
                buf += b
            with self.lock:
                self.fetched = bytes(buf)
                if self.stop_at_end:
                    self.dur = len(buf) / 40000.0          # 320 kb/s
            self.fetch_done.set()
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
            uri = self.track_override if (self.track_override and self.uri) else self.uri
            return self.state, uri, f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


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
            assert action in ("GetTransportInfo", "GetPositionInfo", "GetMediaInfo"), action   # read-only only
            if dev.reads_fail:
                self.send_response(500)
                self.end_headers()
                return
            state, uri, rel = dev.read()
            fields = ({"CurrentTransportState": state, "CurrentTransportStatus": "OK", "CurrentSpeed": "1"}
                      if action == "GetTransportInfo" else
                      {"NrTracks": "1", "MediaDuration": "NOT_IMPLEMENTED", "CurrentURI": dev.media_uri,
                       "CurrentURIMetaData": ""} if action == "GetMediaInfo" else
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
        if v["uri"].startswith("x-rincon-mp3radio://") or "/piano/stream/" in v["uri"]:
            self.dev.play_stream(v["uri"])
        else:
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


STREAM_RE = re.compile(r"^/show/api/v1/piano/stream/([0-9a-f]{32})\.mp3$")


def route_server(folder: Path) -> ThreadingHTTPServer:
    """show_service's GET /api/v1/piano/stream/<id>.mp3 under room's /show door, as api.py does it: the growing file,
    no length (HTTP/1.0: the body ends at close), to its .done."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            m = STREAM_RE.match(self.path)
            p = folder / f"{m.group(1)}.mp3" if m else None
            if p is None or not p.exists():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.end_headers()
            done, seen = folder / f"{m.group(1)}.done", False
            with open(p, "rb") as f:
                while True:
                    b = f.read(65536)
                    if b:
                        self.wfile.write(b)
                    elif seen:
                        return
                    elif done.exists():
                        seen = True
                    else:
                        time.sleep(0.01)
    return _serve(H)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """(out factory, FakeRoom, FakeSonos, events, render path); sonos.UNSUPPORTED cleared; fast watch."""
    monkeypatch.setattr(sonos, "UNSUPPORTED", set())
    monkeypatch.setattr(sonos, "WATCH_HZ", 10.0)
    monkeypatch.setattr(sonos, "END_SLACK_S", 0.4)
    streams = tmp_path / "streams"
    monkeypatch.setenv(sonos.STREAM_DIR_ENV, str(streams))
    reaped: list = []
    monkeypatch.setattr(sonos, "_reap_later", lambda paths, delay_s: reaped.append(([str(p) for p in paths], delay_s)))
    dev = FakeSonos()
    room = FakeRoom(dev)
    servers = [upnp_server(dev), room_server(room), route_server(streams)]
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
        kw = {"stream_dir": streams, "stream_start_ms": 300, "put_after_s": 0.5, "stream_ahead_s": 1.0, **kw}
        return sonos.SonosOutput(ZONE, emit=events.append, room_url=f"http://127.0.0.1:{servers[1].server_port}",
                                 sonos_file=tmp_path / "sonos.json", latency_file=tmp_path / "latency.json",
                                 upnp_port=servers[0].server_port, **kw)
    make.door = f"http://127.0.0.1:{servers[2].server_port}/show"
    make.reaped = reaped
    make.streams = streams
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


# -- streams (render on demand: an MP3 radio stream made as it plays) ---------------------------------------------------
def _sine(seconds, f=440.0, start=0):
    t = (np.arange(int(seconds * SR)) + start) / SR
    x = (0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    return np.stack([x, x], 1)


def _blocks(a, n=512):
    return [a[i:i + n] for i in range(0, len(a), n)]


def _decode(data: bytes, tmp: Path) -> np.ndarray:
    p = tmp / "dec.mp3"
    p.write_bytes(data)
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(p), "-f", "f32le", "-ac", "2",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<f4").reshape(-1, 2)


def _onset(y, thr=0.05):
    return int(np.argmax(np.abs(y[:, 0]) > thr))


class Paced:
    """A Streamer's shape: blocks(position) stays ahead_s ahead of position(); notes how far and if Halted ended it."""

    def __init__(self, seconds, ahead_s=0.25, fail_at_s=None):
        self.a = _sine(seconds)
        self.ahead_s, self.sr, self.fail_at = ahead_s, SR, None if fail_at_s is None else int(fail_at_s * SR)
        self.emitted, self.max_lead, self.halted, self.t0 = 0, 0.0, False, None

    def blocks(self, position):
        from piano_render import pc22
        try:
            while self.emitted < len(self.a):
                pos = position()
                if self.t0 is None:
                    self.t0 = time.monotonic()
                if self.emitted >= pos + self.ahead_s * SR:
                    time.sleep(0.002)
                    continue
                if self.fail_at is not None and self.emitted >= self.fail_at:
                    raise RuntimeError("the engine broke")
                b = self.a[self.emitted:self.emitted + 512]
                self.emitted += len(b)
                self.max_lead = max(self.max_lead, self.emitted / SR - (time.monotonic() - self.t0))
                yield b
        except pc22.Halted:
            self.halted = True
            raise


def test_stream_urls_and_matching(tmp_path):
    sid = "f" * 32
    assert sonos.stream_url(url_of(), sid) == f"x-rincon-mp3radio://lan-host:8700/show/api/v1/piano/stream/{sid}.mp3"
    assert sonos.stream_url("http://lan-host:8700/show/", sid, "http") == \
        f"http://lan-host:8700/show/api/v1/piano/stream/{sid}.mp3"
    with pytest.raises(sonos.SonosError):
        sonos.stream_url("/show/api/v1/piano/audio/x.flac", sid)
    assert sonos.same_uri("x-rincon-mp3radio://h:8700/show/a%20b.mp3", "http://h:8700/show/a b.mp3")
    assert not sonos.same_uri("x-rincon-mp3radio://h/a.mp3", "http://h/b.mp3")
    (tmp_path / "sonos.json").write_text(json.dumps({"zones": {ZONE: {"ip": "127.0.0.1"}}}))
    with pytest.raises(ValueError):
        sonos.SonosOutput(ZONE, sonos_file=tmp_path / "sonos.json", stream_scheme="rtsp")


def test_mp3_stream_is_written_as_it_is_made_and_marked_done(tmp_path, monkeypatch):
    reaped = []
    monkeypatch.setattr(sonos, "_reap_later", lambda paths, d: reaped.append((paths, d)))
    x = _sine(1.5)
    st = sonos.Mp3Stream(_blocks(x), pad_s=0.5, folder=tmp_path, ahead_s=0.5)
    t = time.monotonic()
    st.start()
    sizes = []
    while not st.ended.is_set():
        sizes.append(st.audio_in_file_s())
        time.sleep(0.05)
    wall = time.monotonic() - t
    assert st.error is None and st.frames == int(0.5 * SR) + len(x) and st.duration_s == pytest.approx(2.0)
    assert sizes and sizes[0] < 1.5 and max(sizes) > 1.0 and sorted(sizes) == sizes          # it grew as it went
    assert 1.2 < wall < 3.5                                     # a plain iterable is paced: 0.5 s ahead of the clock
    doc = json.loads(st.done_path.read_text())
    assert doc["frames"] == st.frames and doc["error"] is None and sonos.KEY_RE.match(st.id)
    assert reaped == [([st.path, st.done_path], sonos.STREAM_KEEP_S)]
    y = _decode(st.path.read_bytes(), tmp_path)
    on = _onset(y)
    assert abs(on - (int(0.5 * SR) + sonos.MP3_DELAY_FRAMES)) < 40                # pad + the MP3 delay, then the sine
    assert not np.abs(y[:int(0.5 * SR)]).max() > 1e-3
    n = int(0.5 * SR) + sonos.MP3_DELAY_FRAMES
    assert np.corrcoef(y[n + 2000:n + 40000, 0], x[2000:40000, 0])[0, 1] > 0.99


def test_mp3_stream_paces_a_streamer_shape_by_its_position(tmp_path, monkeypatch):
    monkeypatch.setattr(sonos, "_reap_later", lambda paths, d: None)
    src = Paced(2.0, ahead_s=1.0)
    st = sonos.Mp3Stream(src, folder=tmp_path)
    st.start()
    assert st.ended.wait(10) and st.error is None and src.emitted == 2 * SR
    assert 0.8 < src.max_lead < 1.0 + 0.05                      # its ahead_s ahead of the clock from its start


def test_mp3_stream_stop_ends_the_source_and_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sonos, "_reap_later", lambda paths, d: None)
    src = Paced(30.0)
    st = sonos.Mp3Stream(src, folder=tmp_path, ahead_s=0.5)
    st.start()
    assert st.wait_ready(0.3, 5)
    st.stop()
    assert st.ended.is_set() and src.halted and st.error is None and src.emitted < 3 * SR
    assert json.loads(st.done_path.read_text())["frames"] == st.frames
    with pytest.raises(Exception) as e:
        st.position()
    assert type(e.value).__name__ == "Halted"


def test_mp3_stream_source_error_ends_it_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(sonos, "_reap_later", lambda paths, d: None)
    st = sonos.Mp3Stream(Paced(5.0, fail_at_s=0.5), folder=tmp_path, ahead_s=0.5)
    st.start()
    assert st.ended.wait(10) and st.error == "RuntimeError: the engine broke"
    assert json.loads(st.done_path.read_text())["error"] == st.error and st.audio_in_file_s() > 0.4


def test_sweep_and_the_detached_reaper(tmp_path):
    a, b, c, d = ("a" * 32, "b" * 32, "c" * 32, "d" * 32)
    for sid in (a, b, c, d):
        (tmp_path / f"{sid}.mp3").write_bytes(b"x")
    for sid in (a, b):
        (tmp_path / f"{sid}.done").write_text("{}")
    (tmp_path / "keep.txt").write_text("not a stream")
    old = time.time() - sonos.STREAM_KEEP_S - 5
    for f in (f"{a}.mp3", f"{a}.done", f"{b}.mp3", f"{c}.mp3"):
        os.utime(tmp_path / f, (old, old))                       # a ended long ago; b's .done is fresh; c died
    assert sorted(sonos.sweep_streams(tmp_path)) == [a, c]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([f"{b}.mp3", f"{b}.done", f"{d}.mp3", "keep.txt"])
    sonos._reap_later([tmp_path / f"{b}.mp3", tmp_path / f"{b}.done"], 1)
    deadline = time.monotonic() + 5
    while (tmp_path / f"{b}.mp3").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not (tmp_path / f"{b}.mp3").exists() and not (tmp_path / f"{b}.done").exists()


def test_a_stream_plays_as_a_radio_stream_through_room_service(rig):
    make, room, dev, events, render, tmp = rig
    out = make()
    x = _sine(1.0)
    t_audio0 = out.start(_blocks(x), lead_in_ms=250, url=make.door + "/api/v1/piano/audio/" + KEY + ".flac",
                         title="Piano: Nocturne")
    (t_put, body), = room.puts
    v = body["values"][TARGET]
    st = out.session.stream
    port = make.door.split(":")[2].split("/")[0]
    assert v["uri"] == f"x-rincon-mp3radio://127.0.0.1:{port}/show/api/v1/piano/stream/{st.id}.mp3"
    assert v["fade_s"] == 0 and v["title"] == "Piano: Nocturne" and v["volume"] == 40
    assert t_audio0 == pytest.approx(t_put + 0.3 + 0.25 + sonos.MP3_DELAY_FRAMES / SR, abs=0.05)   # ours: 300 ms
    assert out.wait(10)
    assert out.session.error is None and out.session.done["by"] == "sonos", (out.session.error, out.session.done)
    d = out.session.done
    assert d["stream_s"] == pytest.approx(1.25) and 250 <= d["seen_ms"] < 1500
    assert dev.fetch_done.wait(2) and dev.fetched == st.path.read_bytes()          # the whole stream, as written
    assert dev.gets == [v["uri"].replace("x-rincon-mp3radio://", "http://")]
    y = _decode(dev.fetched, tmp)
    assert abs(_onset(y) - (int(0.25 * SR) + sonos.MP3_DELAY_FRAMES)) < 40
    assert make.reaped and make.reaped[-1][0] == [str(st.path), str(st.done_path)]
    assert room.puts[1:] == [] and set(dev.actions) <= {"GetTransportInfo", "GetPositionInfo", "GetMediaInfo"}


def test_a_stream_still_playing_after_its_end_is_paused(rig, monkeypatch):
    make, room, dev, events, render, tmp = rig
    monkeypatch.setattr(sonos, "STREAM_END_SLACK_S", 0.5)
    dev.stop_at_end = False                                   # a Sonos that sits on (or re-fetches) a finished stream
    out = make()
    out.start(_blocks(_sine(0.6)), lead_in_ms=0, url=make.door)
    assert out.wait(10) and out.session.done["by"] == "stream" and out.session.error is None
    assert room.puts[-1][1]["values"] == {TARGET: "paused"} and dev.state == "PAUSED_PLAYBACK"


def test_a_radio_trackuri_of_its_own_is_matched_on_the_media_uri(rig):
    make, room, dev, events, render, tmp = rig
    dev.track_override = "x-sonosapi-stream:piano?sid=1"
    out = make()
    out.start(_blocks(_sine(0.8)), lead_in_ms=0, url=make.door)
    assert out.wait(10) and out.session.error is None and out.session.done["by"] == "sonos"
    assert "GetMediaInfo" in dev.actions and out.session.track_uri == dev.track_override


def test_relTime_edges_of_a_stream_are_recorded_not_rebased(rig):
    make, room, dev, events, render, tmp = rig
    dev.delay = 1.0                                          # 0.7 s later than our 300 ms
    out = make()
    out.start(Paced(3.6), lead_in_ms=0, url=make.door)
    assert out.wait(15) and out.session.error is None, out.session.error
    assert not [e for e in events if e["ev"] == "rebase"] and out.session.rebased == 0
    assert 400 < out.session.done["rel_start_ms"] < 1100       # what RelTime says: for the mic to check


def test_stop_during_a_stream_pauses_it_and_ends_the_render(rig):
    make, room, dev, events, render, tmp = rig
    src = Paced(30.0)
    out = make()
    out.start(src, lead_in_ms=0, url=make.door)
    time.sleep(1.0)
    out.stop()
    assert room.puts[-1][1]["values"] == {TARGET: "paused"} and dev.state == "PAUSED_PLAYBACK"
    st = out.session.stream
    assert st.ended.is_set() and src.halted and st.done_path.exists() and src.emitted < 4 * SR


def test_a_stream_that_fails_after_the_put_is_an_error(rig):
    make, room, dev, events, render, tmp = rig
    out = make()
    out.start(Paced(5.0, fail_at_s=1.0), lead_in_ms=0, url=make.door)
    assert out.wait(10)
    assert out.session.error == "the piano stream failed: RuntimeError: the engine broke" and out.session.done is None


def test_a_stream_that_never_makes_sound_is_refused_before_the_put(rig, monkeypatch):
    make, room, dev, events, render, tmp = rig
    monkeypatch.setattr(sonos, "STREAM_READY_TIMEOUT_S", 1.0)

    def broken(position):
        raise ValueError("no samples")
        yield                                                # noqa: unreachable: a generator

    with pytest.raises(sonos.SonosError, match="no samples"):
        make().start(broken, lead_in_ms=0, url=make.door)
    assert room.puts == []


def test_a_stop_during_a_streams_start_stops_it_before_the_put(rig, monkeypatch):
    make, room, dev, events, render, tmp = rig
    out = make()
    real = sonos.Mp3Stream.wait_ready

    def slow(self, *a, **kw):
        out.stop()
        return real(self, *a, **kw)
    monkeypatch.setattr(sonos.Mp3Stream, "wait_ready", slow)
    src = Paced(10.0)
    with pytest.raises(sonos.SonosError, match="stopped before"):
        out.start(src, lead_in_ms=0, url=make.door)
    assert room.puts == []
    deadline = time.monotonic() + 3
    while not src.halted and time.monotonic() < deadline:
        time.sleep(0.02)
    assert src.halted


# -- the whole chain: session.py -> Streamer -> SonosOutput -> Mp3Stream -> the route -> the Sonos (all fakes) --------
def test_a_session_streams_a_piece_to_the_sonos_and_times_it(rig, gm_registry, midi):
    """The live path end to end: a real Session on a real Streamer (TimGM6mb) plays on the fake Sonos, which fetches
    the radio stream as it is made; t_audio0 is the PUT + the start delay + the pad left over + the encoder's delay."""
    import queue

    from piano_render import session, stream
    make, room, dev, events, render, tmp = rig
    m = midi([(0.0, "on", 60, 100), (0.4, "off", 60, 64), (0.5, "on", 67, 90), (0.9, "off", 67, 64)], name="tune.mid")
    out = make()
    lines: "queue.Queue[dict]" = queue.Queue()

    class W:
        def write(self, s):
            for ln in s.splitlines():
                lines.put(json.loads(ln))

        def flush(self):
            pass

    def want(ev, timeout=20.0):
        end = time.monotonic() + timeout
        while True:
            msg = lines.get(timeout=max(0.01, end - time.monotonic()))
            if msg["ev"] == "error":
                raise AssertionError(msg)
            if msg["ev"] == ev:
                return msg

    s = session.Session(str(m), "gm", 0.75, proto=session.Proto(W()), output_factory=lambda: out,
                        output=f"sonos:{ZONE}", ahead_s=1.0, loads_file=tmp / "loads.json",
                        streamer_factory=lambda: stream.Streamer(str(m), "gm", registry=gm_registry, ahead_s=1.0))
    r, w = os.pipe()
    cmds, wf = os.fdopen(r, "r"), os.fdopen(w, "w")
    rc: list = []
    th = threading.Thread(target=lambda: rc.append(s.run(cmds)), daemon=True)
    th.start()
    try:
        want("rendered")
        wf.write(json.dumps({"url": make.door, "title": "Piano: Tune"}).join(["play-sonos ", "\n"]))
        wf.flush()
        st = want("started")
        (t_put, body), = room.puts
        v = body["values"][TARGET]
        assert v["uri"].startswith("x-rincon-mp3radio://") and v["title"] == "Piano: Tune"
        pad = 0.75 - 0.3 - sonos.MP3_DELAY_FRAMES / SR                       # what the 300 ms start delay leaves
        assert st["pad_s"] == pytest.approx(pad, abs=1e-3)
        assert st["t_audio0"] == pytest.approx(t_put + 0.3 + pad + sonos.MP3_DELAY_FRAMES / SR, abs=0.05)
        played = want("played")
        assert played["by"] == "sonos" and played["complete"] and played["late_blocks"] == 0
        assert dev.fetch_done.wait(5)
        y = _decode(dev.fetched, tmp)
        assert abs(_onset(y, 0.01) - (round(pad * SR) + sonos.MP3_DELAY_FRAMES)) < 0.01 * SR   # MIDI 0 after the pad
        assert len(y) > (pad + 0.9) * SR
    finally:
        wf.close()
        th.join(10)
    assert rc == [0]
