"""Per-screen stream switches (config/live/stream_switches.json, plan change 2): read by mtime at session start."""
from __future__ import annotations

import json
import os

from stream_service import switches as sw_mod
from stream_service.server import MANAGER
from stream_service.switches import StreamSwitches

from .test_sessions import FakeEngine, create, client  # noqa: F401 (fixture)


def write(path, data, bump=0):
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump))


def test_no_file_means_ffmpeg_everywhere(tmp_path):
    s = StreamSwitches(tmp_path / "stream_switches.json")
    assert s.encoder("south-screen") == "ffmpeg" and s.for_screen("x") == {"encoder": "ffmpeg", "workers": "inproc",
                                                                            "fps": 30, "degrade": "on"}


def test_default_and_per_screen_values(tmp_path):
    p = tmp_path / "stream_switches.json"
    write(p, {"default": {"encoder": "ffmpeg"}, "screens": {"south-screen": {"encoder": "nvenc"}}})
    s = StreamSwitches(p)
    assert s.encoder("south-screen") == "nvenc" and s.encoder("north-screen") == "ffmpeg"
    write(p, {"default": {"encoder": "nvenc"}, "screens": {"north-screen": {"encoder": "ffmpeg"}}}, bump=10**9)
    assert s.encoder("south-screen") == "nvenc" and s.encoder("north-screen") == "ffmpeg"   # re-read on mtime


def test_unchanged_mtime_is_not_reread(tmp_path, monkeypatch):
    p = tmp_path / "stream_switches.json"
    write(p, {"screens": {"devtest": {"encoder": "nvenc"}}})
    s = StreamSwitches(p)
    assert s.encoder("devtest") == "nvenc"
    reads = []
    monkeypatch.setattr(sw_mod.json, "loads", lambda t: reads.append(t) or {})
    assert s.encoder("devtest") == "nvenc" and reads == []


def test_bad_json_keeps_the_last_good_copy(tmp_path):
    p = tmp_path / "stream_switches.json"
    write(p, {"screens": {"devtest": {"encoder": "nvenc"}}})
    s = StreamSwitches(p)
    assert s.encoder("devtest") == "nvenc"
    write(p, "{not json", bump=10**9)
    assert s.encoder("devtest") == "nvenc" and s.errors
    write(p, [1, 2], bump=2 * 10**9)                                  # valid JSON, not an object
    assert s.encoder("devtest") == "nvenc"
    p.unlink()                                                        # removed: back to the defaults
    assert s.encoder("devtest") == "ffmpeg"


def test_unknown_values_and_shapes_are_ignored(tmp_path):
    p = tmp_path / "stream_switches.json"
    write(p, {"default": {"encoder": "x264", "workers": "threads"}, "screens": {"a": "nvenc", "b": {"encoder": "NVENC"},
                                                                           "c": {"encoder": "nvenc"}}})
    s = StreamSwitches(p)
    assert [s.encoder(k) for k in "abcd"] == ["ffmpeg", "ffmpeg", "nvenc", "ffmpeg"]
    assert [s.workers(k) for k in "abcd"] == ["inproc"] * 4
    assert len(s.errors) == 4
    write(p, {"screens": ["devtest"]}, bump=10**9)
    assert s.encoder("devtest") == "ffmpeg" and s.errors == ["screens: not an object"]


def test_workers_per_screen(tmp_path):
    p = tmp_path / "stream_switches.json"
    write(p, {"default": {"workers": "inproc"}, "screens": {"devtest": {"workers": "proc"},
                                                           "south-screen": {"workers": "PROC"}, "x": {"wk": 1}}})
    s = StreamSwitches(p)
    assert [s.workers(k) for k in ("devtest", "south-screen", "north-screen", "x")] == ["proc", "inproc", "inproc",
                                                                                         "inproc"]
    assert s.encoder("devtest") == "ffmpeg" and len(s.errors) == 2


def test_in_dir_and_the_shipped_file():
    s = StreamSwitches.in_dir(None)
    assert s.path == sw_mod.DEFAULT_DIR / "stream_switches.json"
    assert s.path.exists()                                            # shipped: ffmpeg, nvenc on the P4000's screens
    assert [s.encoder(k) for k in ("north-screen", "south-screen", "devtest", "other")] == \
        ["nvenc", "nvenc", "nvenc", "ffmpeg"] and s.errors == []
    assert [s.workers(k) for k in ("north-screen", "south-screen", "devtest", "other")] == \
        ["proc", "proc", "proc", "inproc"]                            # change 3: every TV in a worker
    assert [s.fps(k) for k in ("north-screen", "south-screen", "devtest", "other")] == [60, 60, 30, 30]   # 4K60 on the TVs
    assert {s.degrade(k) for k in ("north-screen", "south-screen", "devtest", "other")} == {"on"}


def test_fps_and_degrade_switches(tmp_path):
    p = tmp_path / "stream_switches.json"
    write(p, {"default": {"fps": 60, "degrade": "off"}, "screens": {"a": {"fps": 30}, "b": {"fps": "60"},
                                                                   "c": {"fps": True, "degrade": "maybe"}}})
    s = StreamSwitches(p)
    assert [s.fps(k) for k in "abcd"] == [30, 60, 60, 60] and s.degrade("a") == "off"
    assert len(s.errors) == 3                                         # "60" (a string), True, "maybe"


async def test_a_session_takes_its_encoder_from_the_file_at_creation(client, tmp_path):  # noqa: F811
    p = tmp_path / "stream_switches.json"
    write(p, {"screens": {"devtest": {"encoder": "nvenc"}}})
    client.app[MANAGER].switches = StreamSwitches(p)
    res, s = await create(client)
    assert s.engine is FakeEngine.instances[-1] and s.engine.encoder == "nvenc"
    summ = s.summary()
    assert "encoder" in summ and "encoder_fallback" in summ and "encode_cpu_ms" in summ["stats"]   # (None: a fake)
    write(p, {"screens": {"devtest": {"encoder": "ffmpeg"}}}, bump=10**9)  # flipped: the next session, no restart
    _, s2 = await create(client, screen="devtest", idempotency_key="second")
    assert s2 is not s and s2.engine.encoder == "ffmpeg" and s.engine.encoder == "nvenc"


async def test_a_sessions_frame_rate_and_degrade_mode_come_from_the_file(client, tmp_path):  # noqa: F811
    p = tmp_path / "stream_switches.json"
    write(p, {"screens": {"devtest": {"fps": 60, "degrade": "off"}}})
    client.app[MANAGER].switches = StreamSwitches(p)
    _, s = await create(client, profile="hevc-2160p30")
    assert s.profile.name == "hevc-2160p60" and s.profile.fps == 60 and s.profile.bitrate == "40M"
    summ = s.summary()                                                # room matches the asked name on adoption
    assert (summ["profile"], summ["profile_live"], summ["fps"]) == ("hevc-2160p30", "hevc-2160p60", 60)
    assert s.attach["fps"] == 60 and s.engine.degrade_mode == "off" and s.media.fps == 60
    write(p, {"screens": {"devtest": {"fps": 30}}}, bump=10**9)
    _, s2 = await create(client, profile="hevc-2160p60", idempotency_key="b")
    assert s2.profile.name == "hevc-2160p30" and s2.engine.degrade_mode == "on"
    _, s3 = await create(client, profile="h264-720p30", idempotency_key="c")   # no 60p sibling: as asked
    assert s3.profile.name == "h264-720p30"
