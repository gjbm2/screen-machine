"""show_service/config.py: sender specs, args over env, latency.json offsets and the mtime watch."""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from show_service import config, contract
from show_service.contract import Sender


# -- sender specs ----------------------------------------------------------------------------------------------------
def test_parse_sender_full():
    s = config.parse_sender("kiosk=screen-machine-drawingroom:6981/room-mic,room-piano")
    assert s == Sender("kiosk", "screen-machine-drawingroom", 6981, ("room-mic", "room-piano"))


def test_parse_sender_defaults():
    s = config.parse_sender(" room=screen-machine-drawingroom:6980 ")
    assert (s.name, s.host, s.port) == ("room", "screen-machine-drawingroom", 6980)
    assert s.streams == contract.KNOWN_STREAMS                       # no streams: ask for every known name
    s = config.parse_sender("pc22.lan/pc22-loop, pc22-mic,pc22-loop")
    assert (s.name, s.host, s.port, s.streams) == ("pc22.lan", "pc22.lan", 6980, ("pc22-loop", "pc22-mic"))


@pytest.mark.parametrize("spec", ["", "=host", "kiosk=", "kiosk=host:notaport", "kiosk=host:70000", "kiosk=host:0",
                                  "kiosk=host/", "kiosk=host/a-stream-name-far-too-long", "bad name=host",
                                  "kiosk=ho st"])
def test_parse_sender_rejects(spec):
    with pytest.raises(ValueError):
        config.parse_sender(spec)


def test_parse_bind():
    assert config.parse_bind("0.0.0.0:6980", ("x", 1)) == ("0.0.0.0", 6980)
    assert config.parse_bind(":9000", ("127.0.0.1", 1)) == ("127.0.0.1", 9000)
    assert config.parse_bind("localhost", ("127.0.0.1", 8703)) == ("localhost", 8703)
    for bad in ("", "host:x", "host:-1"):
        with pytest.raises(ValueError):
            config.parse_bind(bad, ("127.0.0.1", 1))


# -- load ------------------------------------------------------------------------------------------------------------
def test_defaults_without_args_or_env():
    o = config.load_options([], {})
    assert o.config == contract.Config() and o.notes == ()
    assert o.stream_priority is None and o.artnet_target is None          # Art-Net off by default


def test_env_then_args_override():
    env = {"SHOW_SENDERS": "kiosk=screen-machine-drawingroom/room-mic,room-piano; pc22=pc22.lan/pc22-loop",
           "SHOW_VBAN_BIND": "0.0.0.0:6990", "SHOW_BUS_BIND": "127.0.0.1:8799",
           "SHOW_LATENCY_FILE": "config/room/other.json", "SHOW_LOG_LEVEL": "debug"}
    cfg = config.load([], env)
    assert [s.name for s in cfg.senders] == ["kiosk", "pc22"]
    assert cfg.vban_bind == ("0.0.0.0", 6990) and cfg.bus_bind == ("127.0.0.1", 8799)
    assert cfg.latency_file == contract.REPO_ROOT / "config/room/other.json" and cfg.log_level == "DEBUG"
    # args win; any --sender replaces SHOW_SENDERS
    cfg = config.load(["--sender", "room=screen-machine-drawingroom:6980", "--vban-bind", ":7000",
                       "--log-level", "WARN", "--latency-file", "/tmp/x/latency.json"], env)
    assert [s.name for s in cfg.senders] == ["room"] and cfg.vban_bind == ("0.0.0.0", 7000)
    assert cfg.log_level == "WARNING" and cfg.latency_file == pathlib.Path("/tmp/x/latency.json")


def test_ctl_passes_port_and_host():
    """scripts/ctl runs ``python -m show_service --port $SHOW_PORT $SHOW_ARGS``."""
    cfg = config.load(["--port", "8703", "--host", "127.0.0.1", "--sender", "a=h1", "--sender", "b=h2:7000"], {})
    assert cfg.bus_bind == ("127.0.0.1", 8703)
    assert [(s.name, s.port) for s in cfg.senders] == [("a", 6980), ("b", 7000)]
    cfg = config.load(["--bus-bind", "localhost:9000", "--port", "9001"], {"SHOW_BUS_BIND": "127.0.0.1:1"})
    assert cfg.bus_bind == ("localhost", 9001)


def test_bad_values_degrade_with_notes():
    o = config.load_options(["--sender", "kiosk=host:bad", "--sender", "ok=host2", "--sender", "ok=host3",
                             "--vban-bind", "x:y", "--port", "99999", "--log-level", "LOUD", "--frobnicate", "1",
                             "--artnet-target", "nohost:notaport"], {})
    cfg = o.config
    assert [(s.name, s.host) for s in cfg.senders] == [("ok", "host3")]
    assert cfg.vban_bind == contract.Config().vban_bind and cfg.bus_bind == contract.Config().bus_bind
    assert cfg.log_level == "INFO" and o.artnet_target is None
    text = " | ".join(o.notes)
    for bit in ("sender skipped", "given twice", "vban-bind", "--port", "LOUD", "--frobnicate", "artnet target"):
        assert bit in text, bit


def test_extras_outside_config():
    o = config.load_options(["--stream-priority", "room-mic, pc22-loop,room-mic", "--artnet-target",
                             "artnet-node.lan:6455"], {})
    assert o.stream_priority == ("room-mic", "pc22-loop") and o.config.stream_priority == ("room-mic", "pc22-loop")
    assert o.artnet_target == ("artnet-node.lan", 6455)


def test_stream_priority_from_env_and_flag():
    env = {"SHOW_STREAM_PRIORITY": "pc22-loop,room-mic"}
    assert config.load_options([], env).config.stream_priority == ("pc22-loop", "room-mic")
    o = config.load_options(["--stream-priority", "room-line"], env)
    assert o.config.stream_priority == ("room-line",)
    o = config.load_options(["--stream-priority", "pc22-loop,much-too-long-stream-name"], {})
    assert o.config.stream_priority == ("pc22-loop",) and any("stream priority" in n for n in o.notes)


def test_artnet_target_flag_over_env_and_off_by_default():
    env = {"SHOW_ARTNET_TARGET": "127.0.0.1:6454"}
    assert config.load_options([], {}).artnet_target is None
    assert config.load_options([], env).artnet_target == ("127.0.0.1", 6454)
    assert config.load_options([], {"SHOW_ARTNET_TARGET": "localhost"}).artnet_target == ("localhost", 6454)
    assert config.load_options(["--artnet-target", "off"], env).artnet_target is None
    assert config.load_options([], {"SHOW_ARTNET_TARGET": ""}).artnet_target is None
    o = config.load_options([], {"SHOW_ARTNET_TARGET": "host:0"})
    assert o.artnet_target is None and "Art-Net out off" in o.notes[0]


def test_no_literal_ips_as_sender_defaults():
    src = pathlib.Path(config.__file__).read_text()
    assert "192.168." not in src and "10.0." not in src


# -- latency.json ----------------------------------------------------------------------------------------------------
def _write(path: pathlib.Path, obj) -> None:
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def test_acoustic_offsets(tmp_path):
    p = tmp_path / "latency.json"
    assert config.acoustic_offsets(p) == {}                                   # absent
    _write(p, {"version": 2, "outputs": {}, "sources": {}})
    assert config.acoustic_offsets(p) == {}
    _write(p, {"version": 2, "sources": {"room-mic": {"acoustic_offset_ms": 3}, "pc22-loop": {"acoustic_offset_ms":
               412.5}, "bad": {"acoustic_offset_ms": "x"}, "flag": {"acoustic_offset_ms": True}, "none": {}, "x": 5}})
    assert config.acoustic_offsets(p) == {"room-mic": 3.0, "pc22-loop": 412.5}
    _write(p, '{"sources": {"a": {"acoustic_offset_ms": NaN}}}')
    assert config.acoustic_offsets(p) == {}
    _write(p, "{torn")
    assert config.acoustic_offsets(p) == {}
    _write(p, "[1, 2]")
    assert config.acoustic_offsets(p) == {}
    assert config.acoustic_offsets(tmp_path) == {}                            # a directory: never raises


def test_the_real_latency_file_reads():
    assert isinstance(config.acoustic_offsets(contract.Config().latency_file), dict)


def test_latency_watch(tmp_path):
    p = tmp_path / "latency.json"
    w = config.LatencyWatch(p)
    assert w.poll(force=True) == {}                  # absent at start
    assert w.poll() is None
    _write(p, {"sources": {"room-mic": {"acoustic_offset_ms": 2.0}}})
    assert w.poll() == {"room-mic": 2.0}
    assert w.poll() is None                          # unchanged
    _write(p, '{"sources": {"room-mic": ')           # a torn write: keep the last offsets, retry next poll
    os.utime(p, ns=(1, 1))
    assert w.poll() is None and w.errors == 1 and w.last == {"room-mic": 2.0}
    _write(p, {"sources": {"room-mic": {"acoustic_offset_ms": -4.0}}})
    assert w.poll() == {"room-mic": -4.0}
    p.unlink()
    assert w.poll() == {}                            # gone: offsets back to 0


def test_beat_thread_flag_over_env_off_by_default():
    """README S4.5, S4.12: SHOW_BEAT_THREAD / --beat-thread, off by default (measured: the thread cost more than it
    saved in the real process) and in an Options built in code."""
    assert config.load_options([], {}).beat_thread is False
    assert config.Options(config=contract.Config()).beat_thread is False
    assert config.load_options([], {"SHOW_BEAT_THREAD": "on"}).beat_thread is True
    assert config.load_options(["--beat-thread", "off"], {"SHOW_BEAT_THREAD": "on"}).beat_thread is False
    assert config.load_options(["--beat-thread", "on"], {}).beat_thread is True
    o = config.load_options([], {"SHOW_BEAT_THREAD": "sideways"})
    assert o.beat_thread is False and any("beat thread" in n for n in o.notes)


def test_beat_process_and_rx_coalesce_options():
    """SHOW_BEAT_PROC / --beat-proc (off by default and in code), SHOW_BEAT_NICE 0..19 and SHOW_RX_COALESCE_MIN
    1..32 (default 1: show-rx never waits); bad values are notes, clamped or defaulted."""
    o = config.load_options([], {})
    assert (o.beat_proc, o.beat_nice, o.rx_coalesce_min, o.notes) == (False, 0, 1, ())
    assert config.Options(config=contract.Config()).beat_proc is False
    o = config.load_options([], {"SHOW_BEAT_PROC": "on", "SHOW_BEAT_NICE": "5", "SHOW_RX_COALESCE_MIN": "4"})
    assert (o.beat_proc, o.beat_nice, o.rx_coalesce_min) == (True, 5, 4)
    assert config.load_options(["--beat-proc", "off"], {"SHOW_BEAT_PROC": "on"}).beat_proc is False
    o = config.load_options([], {"SHOW_BEAT_PROC": "maybe", "SHOW_BEAT_NICE": "-3", "SHOW_RX_COALESCE_MIN": "x"})
    assert (o.beat_proc, o.beat_nice, o.rx_coalesce_min) == (False, 0, 1) and len(o.notes) == 3
