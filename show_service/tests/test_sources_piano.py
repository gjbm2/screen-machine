"""The test piano's sound, output and recordings (show_service/sources.py, 22 Oct): argv and state mapping for
instrument / output / once / rec:, the status file, the runtime acoustic offset, GET/POST /api/v1/test's new fields,
and __main__'s offset merge. A fake popen: nothing is spawned, nothing plays; tmp folders only."""
from __future__ import annotations

import json
import sys
import threading
import types

import pytest

from show_service import __main__ as main_mod
from show_service import contract, sources
from show_service.contract import BrainContext

PY = "/usr/bin/python3-test"
NEW_SENDER = """ap.add_argument("--test-piano")\nap.add_argument("--no-speaker")\nap.add_argument("--instrument")
ap.add_argument("--output")\nap.add_argument("--status-file")\nap.add_argument("--lead-ms")\nap.add_argument("--once")
"""
OLD_SENDER = """ap.add_argument("--test-piano")\nap.add_argument("--no-speaker")\n"""
SONOS_SENDER = NEW_SENDER + """ap.add_argument("--title")\nSONOS_PREFIX = "sonos:"\n"""


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeProc:
    _pid = 4000

    def __init__(self, argv) -> None:
        FakeProc._pid += 1
        self.pid, self.argv, self.rc, self.terminated = FakeProc._pid, list(argv), None, False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True
        self.rc = -15

    def kill(self):
        self.rc = -9

    def wait(self, timeout=None):
        return self.rc


class Popen:
    def __init__(self) -> None:
        self.procs: list[FakeProc] = []

    def __call__(self, argv, **kw):
        p = FakeProc(argv)
        self.procs.append(p)
        return p

    @property
    def argv(self) -> list[str]:
        return self.procs[-1].argv


class Health:
    def __init__(self, clock: Clock) -> None:
        self.clock, self.live = clock, False

    def __call__(self) -> dict:
        if not self.live:
            return {"streams": {}}
        return {"t_ms": self.clock.t * 1000.0, "streams": {"test-piano": {"state": "streaming", "net": {"age_ms": 1}}}}


def settings(tmp, **piano) -> dict:
    return {"senders": {"test-piano": {"hosts": ["localhost"], "port": 40002, "streams": ["test-piano"],
                                       "on_demand": True},
                        "test-audio": {"hosts": ["localhost"], "port": 40001, "streams": ["test-audio"],
                                       "on_demand": True}},
            "tests": {"piano": {"sender": "test-piano", "pieces": str(tmp / "piano"), **piano},
                      "audio": {"sender": "test-audio", "music": str(tmp / "music")}}}


@pytest.fixture
def lib(tmp_path):
    (tmp_path / "piano").mkdir()
    (tmp_path / "piano" / "Nocturne.mid").write_bytes(b"MThd")
    (tmp_path / "music").mkdir()
    (tmp_path / "music" / "a.mp3").write_bytes(b"ID3")
    (tmp_path / "sf").mkdir()
    (tmp_path / "sf" / "salamander.sf2").write_bytes(b"RIFF")
    (tmp_path / "show_sender.py").write_text(NEW_SENDER)              # never run: the popen is fake
    (tmp_path / "old_sender.py").write_text(OLD_SENDER)
    (tmp_path / "sonos_sender.py").write_text(SONOS_SENDER)
    return tmp_path


def instruments(lib, **extra) -> None:
    doc = {"root": str(lib / "sf"), "instruments": [
        {"id": "salamander", "label": "Grand (Yamaha C5)", "engine": "fluidsynth", "file": "salamander.sf2",
         "order": 2},
        {"id": "windows-gs", "label": "Basic (Windows synth)", "engine": "windows-gs", "order": 1},
        {"id": "steinway", "label": "Steinway D", "engine": "sfizz", "file": "missing/steinway.sfz", "order": 0}],
        **extra}
    (lib / "instruments.json").write_text(json.dumps(doc))


def make(lib, *, seam: bool = True, script: str = "show_sender.py", **piano):
    """A Sources on tmp files only (instruments.json, sonos.json: lib's)."""
    clock = Clock()
    health = Health(clock)
    popen = Popen()
    offsets: list[tuple] = []
    ctx = BrainContext(publish=lambda m: None, set_senders=lambda s: None, set_priority=lambda p: None, health=health)
    src = sources.Sources(ctx, python=PY, piano_script=lib / script, log_dir=lib / "state", clock=clock, popen=popen,
                          pdeath=False, instruments_file=lib / "instruments.json", recordings_dir=lib / "recs",
                          set_offset=(lambda s, ms: offsets.append((s, ms))) if seam else None,
                          sonos_file=lib / "sonos.json")
    src.configure(settings(lib, **piano))
    src.start()
    return types.SimpleNamespace(src=src, clock=clock, health=health, popen=popen, offsets=offsets)


def today(lib, piece: str = "Nocturne.mid", script: str = "show_sender.py") -> list[str]:
    return [PY, str(lib / script), "--test-piano", str(lib / "piano" / piece), "--port", "40002", "--bind",
            "127.0.0.1"]


# -- choices and today's behaviour ------------------------------------------------------------------------------------
def test_no_instruments_file_offers_the_windows_synth_and_keeps_todays_argv(lib):
    r = make(lib)
    ch = r.src.choices()
    assert ch == {"instruments": [{"id": "windows-gs", "label": "Basic (Windows synth)", "kind": "live"}],
                  "default_instrument": "windows-gs",
                  "outputs": [{"id": "pc22", "label": "PC22 speakers"}, {"id": "none", "label": "Screens only"}]}
    off = r.src.status()["piano"]
    assert off["state"] == "off" and off["instrument"] == "windows-gs" and off["output"] == "pc22"
    assert off["once"] is False and off["progress"] is None
    st = r.src.start_test("piano", "file:Nocturne.mid")
    assert r.popen.argv == today(lib)                                  # byte-identical to before
    assert st["instrument"] == "windows-gs" and st["output"] == "pc22" and st["once"] is False
    assert st["progress"] is None and r.offsets == []
    r.src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs", output="pc22", once=False)
    r.src.tick(0.0)
    assert r.popen.argv == today(lib)
    assert r.src.status()["audio"]["instrument"] is None and r.src.status()["audio"]["output"] is None
    r.src.stop()


def test_windows_synth_screens_only_and_once(lib):
    r = make(lib)
    r.src.start_test("piano", "file:Nocturne.mid", output="none")
    assert r.popen.argv == today(lib) + ["--no-speaker"]
    r.src.start_test("piano", "file:Nocturne.mid", output="none", once=True)
    r.src.tick(0.0)
    assert r.popen.argv == today(lib) + ["--no-speaker", "--once"] and r.offsets == []
    r.src.stop()


def test_instruments_json_offers_installed_ones_in_order_with_its_default(lib):
    instruments(lib, default="salamander")
    r = make(lib)
    ch = r.src.choices()
    assert ch["instruments"] == [{"id": "windows-gs", "label": "Basic (Windows synth)", "kind": "live"},
                                 {"id": "salamander", "label": "Grand (Yamaha C5)", "kind": "rendered"}]
    assert ch["default_instrument"] == "salamander"                    # steinway's file is missing: not offered
    with pytest.raises(ValueError, match="unknown_instrument"):
        r.src.start_test("piano", "file:Nocturne.mid", instrument="steinway")
    with pytest.raises(ValueError, match="unknown_output"):
        r.src.start_test("piano", "file:Nocturne.mid", output="sonos")
    instruments(lib, default="steinway")                               # not installed: the windows-gs one
    r.clock.t += 5.0
    assert r.src.choices()["default_instrument"] == "windows-gs"
    (lib / "instruments.json").write_text("{not json")
    r.clock.t += 5.0
    assert r.src.choices()["instruments"] == [{"id": "windows-gs", "label": "Basic (Windows synth)", "kind": "live"}]
    r.src.tick(0.0)
    assert any("instruments.json" in e for e in r.src.snapshot()["errors"])
    st = r.src.start_test("audio", "file:a.mp3", instrument="nope")   # test audio ignores the piano's words
    assert st["instrument"] is None and st["output"] is None
    r.src.stop()


def test_an_older_show_sender_keeps_todays_argv_whatever_is_asked(lib):
    """Before show_sender takes the new flags: only the Windows synth is offered (and is the default), a rendered
    request is refused with the choices, a recording loops (no --once), no offset."""
    instruments(lib, default="salamander")
    r = make(lib, script="old_sender.py")
    ch = r.src.choices()
    assert ch["instruments"] == [{"id": "windows-gs", "label": "Basic (Windows synth)", "kind": "live"}]
    assert ch["default_instrument"] == "windows-gs"
    st = r.src.start_test("piano", "file:Nocturne.mid")
    assert r.popen.argv == today(lib, script="old_sender.py") and st["instrument"] == "windows-gs"
    with pytest.raises(ValueError, match="unknown_instrument"):
        r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    st = r.src.start_test("piano", "file:Nocturne.mid", once=True, output="none")
    assert r.popen.argv == today(lib, script="old_sender.py") + ["--no-speaker"] and st["once"] is False
    assert r.offsets == []
    (lib / "old_sender.py").write_text(NEW_SENDER + "# updated\n")       # the sender learns them: no restart needed
    assert [x["id"] for x in r.src.choices()["instruments"]] == ["windows-gs", "salamander"]
    r.src.stop()


# -- a rendered run: argv, the status file, the offset ----------------------------------------------------------------------
def test_a_rendered_run_prepares_plays_finishes_and_carries_the_offset(lib):
    instruments(lib)
    r = make(lib)
    status = lib / "state" / "test-piano.status.json"
    (lib / "state").mkdir()
    status.write_text(json.dumps({"stage": "done", "progress": 1.0, "error": None}))    # the last run's: removed
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True)
    assert r.popen.argv == today(lib) + ["--instrument", "salamander", "--output", "pc22", "--status-file",
                                         str(status), "--lead-ms", "650", "--once"]
    assert not status.exists()
    assert st["state"] == "starting" and r.offsets == [("test-piano", 650.0)]
    assert r.src.snapshot()["offsets_ms"] == {"test-piano": 650.0}
    status.write_text(json.dumps({"stage": "preparing", "progress": 0.25, "error": None}))
    r.clock.t += 0.1
    r.src.tick(0.0)
    s = r.src.status()["piano"]
    assert s["state"] == "preparing" and s["progress"] == 0.25 and s["error"] is None
    r.health.live = True                                               # its stream answering while it renders
    r.clock.t += 30.0                                                  # a long render: no timeout
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "preparing"
    status.write_text(json.dumps({"stage": "playing", "progress": 0.0}))
    r.health.live = False
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "starting"
    r.health.live = True
    r.clock.t += 0.1
    r.src.tick(0.0)
    s = r.src.status()["piano"]
    assert s["state"] == "playing" and s["progress"] == 0.0 and s["instrument"] == "salamander"
    status.write_text(json.dumps({"stage": "done", "progress": 1.0}))
    r.popen.procs[-1].rc = 0                                           # a once-only play ends by itself
    r.clock.t += 0.1
    r.src.tick(0.0)
    s = r.src.status()["piano"]
    assert s["state"] == "finished" and s["error"] is None and s["progress"] == 1.0 and s["once"] is True
    assert r.offsets[-1] == ("test-piano", None) and r.src.snapshot()["offsets_ms"] == {}
    assert r.src.stop_test("piano") == [] and r.src.status()["piano"]["state"] == "off"
    r.src.stop()


def test_lead_ms_from_show_yaml_screens_only_and_no_seam(lib):
    instruments(lib)
    r = make(lib, lead_ms=480)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    assert r.popen.argv[-2:] == ["--lead-ms", "480"] and r.offsets == [("test-piano", 480.0)]
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="none")
    r.src.tick(0.0)
    argv = r.popen.argv
    assert "--lead-ms" not in argv and argv[argv.index("--output") + 1] == "none"
    assert r.offsets[-1] == ("test-piano", None)                       # screens only: no offset
    r.src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs")
    r.src.tick(0.0)
    assert r.popen.argv == today(lib) and r.offsets[-1] == ("test-piano", None)
    r.src.stop()
    q = make(lib, seam=False)
    q.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    assert "--lead-ms" not in q.popen.argv and "--status-file" in q.popen.argv     # no seam: no offset, no lead
    q.src.stop()
    z = make(lib, lead_ms=0)
    z.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    assert "--lead-ms" not in z.popen.argv and z.offsets == []
    z.src.stop()


def test_a_stop_and_a_failure_remove_the_offset_and_say_why(lib):
    instruments(lib)
    r = make(lib)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    assert r.src.stop_test("piano") == ["piano"] and r.offsets[-1] == ("test-piano", None)
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "off"
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    r.src.tick(0.0)
    (lib / "state" / "test-piano.status.json").write_text(json.dumps({"stage": "error", "error": "no soundfont"}))
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["error"] == "no soundfont"          # shown while it is still exiting
    r.popen.procs[-1].rc = 1
    r.clock.t += 0.1
    r.src.tick(0.0)
    s = r.src.status()["piano"]
    assert s["state"] == "failed" and s["error"] == "no soundfont" and r.offsets[-1] == ("test-piano", None)
    r.src.start_test("piano", "file:Nocturne.mid")                     # a looping run exiting 0 is still failed
    r.src.tick(0.0)
    r.popen.procs[-1].rc = 0
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "failed"
    r.src.stop()


# -- Play on Sonos --------------------------------------------------------------------------------------------------------------
def sonos_json(lib, **zones) -> None:
    (lib / "sonos.json").write_text(json.dumps({"_comment": "test", "zones": zones or {
        "drawing-room": {"room": "Drawing Room", "ip": "192.0.2.17"}}}))


def test_sonos_zones_are_outputs_for_a_sender_that_plays_on_sonos(lib):
    instruments(lib)
    sonos_json(lib, **{"drawing-room": {"room": "Drawing Room", "ip": "192.0.2.17"}, "bad zone": {"ip": "x"},
                       "study": {"ip": "192.0.2.18"}})
    r = make(lib, script="sonos_sender.py")
    assert r.src.choices()["outputs"] == [{"id": "pc22", "label": "PC22 speakers"},
                                          {"id": "none", "label": "Screens only"},
                                          {"id": "sonos:drawing-room", "label": "Sonos (Drawing Room)"},
                                          {"id": "sonos:study", "label": "Sonos (study)"}]
    r.src.tick(0.0)
    assert any("bad zone" in e for e in r.src.snapshot()["errors"])
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:drawing-room")
    assert r.popen.argv == today(lib, script="sonos_sender.py") + [
        "--instrument", "salamander", "--output", "sonos:drawing-room", "--status-file",
        str(lib / "state" / "test-piano.status.json"), "--title", "Nocturne", "--lead-ms", "650"]
    assert st["output"] == "sonos:drawing-room" and r.offsets == [("test-piano", 650.0)]     # the offset applies
    with pytest.raises(ValueError, match="unknown_output"):
        r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:kitchen")
    r.src.stop()


def test_the_windows_synth_asked_for_on_sonos_plays_on_pc22_and_says_so(lib):
    instruments(lib)
    sonos_json(lib)
    r = make(lib, script="sonos_sender.py")
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs", output="sonos:drawing-room")
    assert r.popen.argv == today(lib, script="sonos_sender.py") and st["output"] == "pc22"
    assert st["error"] == sources.LIVE_ON_SONOS and r.offsets == []
    r.src.stop()


def test_no_sonos_without_the_senders_flags_a_rendered_sound_or_the_file(lib):
    instruments(lib)
    sonos_json(lib)
    r = make(lib)                                                     # a show_sender without "sonos:"/--title
    assert [o["id"] for o in r.src.choices()["outputs"]] == ["pc22", "none"]
    with pytest.raises(ValueError, match="unknown_output"):
        r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:drawing-room")
    r.src.stop()
    (lib / "instruments.json").unlink()                               # the Windows synth alone: nothing to render
    q = make(lib, script="sonos_sender.py")
    assert [o["id"] for o in q.src.choices()["outputs"]] == ["pc22", "none"]
    q.src.stop()
    instruments(lib)
    (lib / "sonos.json").unlink()
    z = make(lib, script="sonos_sender.py")
    assert [o["id"] for o in z.src.choices()["outputs"]] == ["pc22", "none"]
    z.src.tick(0.0)
    assert not any("sonos" in e for e in z.src.snapshot()["errors"])
    (lib / "sonos.json").write_text("{nope")
    z.clock.t += 5.0
    assert [o["id"] for o in z.src.choices()["outputs"]] == ["pc22", "none"]
    z.src.tick(0.0)
    assert any("sonos.json" in e for e in z.src.snapshot()["errors"])
    sonos_json(lib)
    z.clock.t += 5.0                                                  # re-read: no restart needed
    assert [o["id"] for o in z.src.choices()["outputs"]][-1] == "sonos:drawing-room"
    z.src.stop()


# -- recordings as pieces -------------------------------------------------------------------------------------------------------
def test_a_recording_plays_once_by_its_absolute_path_and_is_not_a_library_piece(lib):
    rid = "20261022-140512-ab12"
    (lib / "recs").mkdir()
    (lib / "recs" / f"{rid}.mid").write_bytes(b"MThd")
    (lib / "recs" / f"{rid}.json").write_text(json.dumps({"id": rid, "title": "Take - Thu 22 Oct 14:05"}))
    r = make(lib)
    assert all(not p["id"].startswith("rec:") for p in r.src.pieces()["piano"])
    st = r.src.start_test("piano", f"rec:{rid}")
    assert st["label"] == "Take - Thu 22 Oct 14:05" and st["piece"] == f"rec:{rid}" and st["once"] is True
    assert r.popen.argv == [PY, str(lib / "show_sender.py"), "--test-piano", str(lib / "recs" / f"{rid}.mid"),
                            "--port", "40002", "--bind", "127.0.0.1", "--once"]
    r.src.start_test("piano", f"rec:{rid}", once=False)
    r.src.tick(0.0)
    assert "--once" not in r.popen.argv
    for bad in ("rec:20991231-000000-ffff", "rec:../../etc/passwd", "rec:"):
        with pytest.raises(ValueError, match="unknown_piece"):
            r.src.start_test("piano", bad)
    r.popen.procs[-1].rc = 0
    r.src.start_test("piano", f"rec:{rid}")
    r.src.tick(0.0)
    r.popen.procs[-1].rc = 0
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "finished"            # the Windows synth, once: no status file
    r.src.stop()


def test_recordings_folder_from_show_yaml(lib):
    rid = "20261022-140512-cd34"
    (lib / "mine").mkdir()
    (lib / "mine" / f"{rid}.mid").write_bytes(b"MThd")
    (lib / "mine" / f"{rid}.json").write_text(json.dumps({"id": rid, "title": "Mine"}))
    r = make(lib, recordings=str(lib / "mine"))
    assert r.src.start_test("piano", f"rec:{rid}")["label"] == "Mine"
    r.src.stop()


# -- the routes -----------------------------------------------------------------------------------------------------------
def test_the_test_routes_carry_the_new_fields_and_check_them(lib):
    from show_service.tests.test_api import FakeBrain, body, make_api, run, served
    instruments(lib)
    r = make(lib)
    brain = FakeBrain([r.src])
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                b = await body(await cl.get("/api/v1/test"))
                assert [x["id"] for x in b["instruments"]] == ["windows-gs", "salamander"]
                assert b["default_instrument"] == "windows-gs" and [o["id"] for o in b["outputs"]] == ["pc22", "none"]
                assert b["sources"]["piano"]["instrument"] == "windows-gs"
                assert all(not p["id"].startswith("rec:") for p in b["pieces"]["piano"])
                rr = await cl.post("/api/v1/test", json={"source": "piano", "piece": "file:Nocturne.mid",
                                                          "instrument": "steinway"})
                b = await body(rr)
                assert rr.status == 400 and b["error"] == "unknown_instrument"
                assert b["choices"] == ["windows-gs", "salamander"]
                rr = await cl.post("/api/v1/test", json={"source": "piano", "output": "sonos"})
                b = await body(rr)
                assert rr.status == 400 and b["error"] == "unknown_output" and b["choices"] == ["pc22", "none"]
                rr = await cl.post("/api/v1/test", json={"source": "piano", "once": "yes"})
                assert rr.status == 400 and (await body(rr))["error"] == "bad_request"
                rr = await cl.post("/api/v1/test", json={"source": "piano", "piece": "file:Nocturne.mid",
                                                          "instrument": "salamander", "output": "none", "once": True})
                b = await body(rr)
                assert rr.status == 200 and b["instrument"] == "salamander" and b["output"] == "none" and b["once"]
                rr = await cl.post("/api/v1/test", json={"source": "piano", "piece": "file:Nocturne.mid"})
                b = await body(rr)
                assert b["instrument"] == "windows-gs" and b["output"] == "pc22" and b["once"] is False
        run(go())
    finally:
        brain.close()
        r.src.stop()
    assert r.popen.argv == today(lib)


# -- __main__: runtime offsets over latency.json's ---------------------------------------------------------------------------
def test_runtime_offsets_merge_over_latency_json_and_come_off_again():
    class Rx:
        def __init__(self) -> None:
            self.got: list[dict] = []

        def set_offsets(self, o) -> None:
            self.got.append(dict(o))

    show = types.SimpleNamespace(offsets={}, _runtime_offsets={}, _offsets_lock=threading.Lock(), rx=Rx())
    main_mod.Show.set_offsets(show, {"room-mic": 3.0, "test-piano": 10.0})
    assert show.rx.got[-1] == {"room-mic": 3.0, "test-piano": 10.0}
    main_mod.Show.set_runtime_offset(show, "test-piano", 650.0)
    assert show.rx.got[-1] == {"room-mic": 3.0, "test-piano": 650.0}
    main_mod.Show.set_offsets(show, {"room-mic": 4.0})                 # latency.json changes: the run's stays
    assert show.rx.got[-1] == {"room-mic": 4.0, "test-piano": 650.0} and show.offsets == {"room-mic": 4.0}
    main_mod.Show.set_runtime_offset(show, "test-piano", None)
    assert show.rx.got[-1] == {"room-mic": 4.0}


def test_main_wires_the_sources_offset_seam(monkeypatch, tmp_path):
    from show_service.tests.test_main import _brain_opts, _with_seams, install_brain_fakes, install_fakes
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    install_brain_fakes(monkeypatch)
    seen: list = []

    class Src:
        name, tick_hz = "sources", 1.0

        def __init__(self, ctx, **kw) -> None:
            pass

        def set_offset_fn(self, fn) -> None:
            seen.append(fn)

        def provides(self):
            return {}

        def configure(self, s):
            pass

        def restore(self, s):
            pass

        def start(self):
            pass

        def tick(self, now):
            pass

        def snapshot(self):
            return {}

        def persist(self):
            return None

        def stop(self):
            pass
    m = types.ModuleType("show_service.sources")
    m.attach = lambda ctx, **kw: Src(ctx, **kw)
    monkeypatch.setitem(sys.modules, "show_service.sources", m)
    show = main_mod.Show(_brain_opts(tmp_path))
    show.start()
    try:
        assert seen == [show.set_runtime_offset]
        show.set_offsets({"room-mic": 2.0})
        seen[0]("test-piano", 650.0)
        assert show.rx.offsets == {"room-mic": 2.0, "test-piano": 650.0}
    finally:
        show.stop()
    assert contract.TEST_PIANO_STREAM == "test-piano"


# -- the calibration mic: derived, named to show_sender, claimed while a Sonos run plays (1c, 23 Oct) ------------------
CAL_SENDER = SONOS_SENDER + """ap.add_argument("--calibration-mic")\n"""
ROOM_SENDERS = {"kiosk": {"hosts": ["192.0.2.10", "192.0.2.92"], "port": 6980, "streams": ["room-piano", "room-mic"],
                          "label": "Drawing room"},
                "pc22": {"hosts": ["192.0.2.205"], "port": 6984, "streams": ["pc22-mic"], "label": "PC22"}}


def cal_make(lib, *, seam: bool = True, claim_fails: bool = False, **piano):
    """A Sources on a show_sender that takes --calibration-mic, show.yaml's room senders, and a claim seam that
    records every call (and fails while ``r.fail`` is set)."""
    instruments(lib)
    sonos_json(lib, **{"drawing-room": {"room": "Drawing Room", "ip": "192.0.2.17"}, "study": {"ip": "192.0.2.18"}})
    (lib / "cal_sender.py").write_text(CAL_SENDER)
    r = make(lib, script="cal_sender.py", **piano)
    r.claims, r.fail = [], claim_fails

    def claim(stream, on):
        if r.fail:
            raise RuntimeError("mic busy")
        r.claims.append((stream, on))
    if seam:
        r.src.set_mic_claim_fn(claim)
    r.src.configure({**settings(lib, **piano), "senders": {**settings(lib)["senders"], **ROOM_SENDERS}})
    return r


def cal_argv(r) -> list[str]:
    argv = r.popen.argv
    return argv[argv.index("--calibration-mic"):argv.index("--calibration-mic") + 2] if "--calibration-mic" in argv \
        else []


def test_a_sonos_run_claims_its_rooms_mic_and_names_it_to_show_sender(lib):
    r = cal_make(lib)
    assert sources.sender_flags(lib / "cal_sender.py") == frozenset(sources.SENDER_FLAGS) - {"--speed", "--start-s"}
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:drawing-room")
    assert st["state"] == "starting" and r.claims == [("room-mic", True)]       # room-piano is MIDI: not the mic
    assert cal_argv(r) == ["--calibration-mic", "room-mic"]
    snap = r.src.snapshot()
    assert snap["mic_claim"] == "room-mic" and snap["calibration"] == {"piano": {
        "output": "sonos:drawing-room", "mic": "room-mic", "why": "show.yaml sender kiosk (Drawing room)",
        "claimed": True}}
    (lib / "state" / "test-piano.status.json").write_text(json.dumps({"stage": "preparing", "progress": 0.1}))
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "preparing"
    (lib / "state" / "test-piano.status.json").write_text(json.dumps({"stage": "playing", "progress": 0.0}))
    r.health.live = True
    r.clock.t += 0.1
    r.src.tick(0.0)
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "playing" and r.claims == [("room-mic", True)]   # held throughout
    assert r.src.stop_test("piano") == ["piano"] and r.claims[-1] == ("room-mic", False)       # stopped: released
    assert r.src.snapshot()["mic_claim"] is None and r.src.snapshot()["calibration"] == {}
    r.src.stop()
    assert r.claims == [("room-mic", True), ("room-mic", False)]


def test_the_claim_goes_on_every_exit_path(lib):
    r = cal_make(lib)
    sonos = dict(instrument="salamander", output="sonos:drawing-room")
    # a failure
    r.src.start_test("piano", "file:Nocturne.mid", **sonos)
    r.popen.procs[-1].rc = 1
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "failed" and r.claims[-1] == ("room-mic", False)
    # a once-only run that finishes
    r.src.start_test("piano", "file:Nocturne.mid", once=True, **sonos)
    r.src.tick(0.0)
    assert r.claims[-1] == ("room-mic", True)
    r.popen.procs[-1].rc = 0
    r.clock.t += 0.1
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] == "finished" and r.claims[-1] == ("room-mic", False)
    # replaced by another Sonos run (the claim stays: no churn), then by PC22, then by screens only
    r.src.start_test("piano", "file:Nocturne.mid", **sonos)
    n = len(r.claims)
    r.src.start_test("piano", "file:Nocturne.mid", **sonos)
    assert len(r.claims) == n and r.claims[-1] == ("room-mic", True)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="pc22")
    assert r.claims[-1] == ("room-mic", False)
    r.src.start_test("piano", "file:Nocturne.mid", **sonos)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="none")
    assert r.claims[-1] == ("room-mic", False)
    # the windows synth replaces it
    r.src.start_test("piano", "file:Nocturne.mid", **sonos)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs")
    assert r.claims[-1] == ("room-mic", False)
    # the service stops while it plays
    r.src.start_test("piano", "file:Nocturne.mid", **sonos)
    assert r.claims[-1] == ("room-mic", True)
    r.src.stop()
    assert r.claims[-1] == ("room-mic", False) and r.src.snapshot()["mic_claim"] is None
    assert all(on is False for _s, on in r.claims[1::2]) and all(on for _s, on in r.claims[0::2])  # alternating


def test_no_claim_for_pc22_screens_only_or_the_windows_synth(lib):
    r = cal_make(lib)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="pc22")
    assert r.claims == [] and cal_argv(r) == ["--calibration-mic", "pc22-mic"]   # named, never claimed
    assert r.src.snapshot()["calibration"]["piano"]["claimed"] is False
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="none")
    r.src.tick(0.0)
    assert r.claims == [] and cal_argv(r) == [] and r.src.snapshot()["calibration"] == {}
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs", output="sonos:drawing-room")
    r.src.tick(0.0)
    assert st["output"] == "pc22" and r.claims == [] and cal_argv(r) == []     # GS synth: today's argv, no mic
    assert r.popen.argv == today(lib, script="cal_sender.py")
    r.src.stop()
    assert r.claims == []


def test_a_sonos_zone_no_mic_hears_gets_an_empty_mic_and_no_claim(lib):
    r = cal_make(lib)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:study")
    assert r.claims == [] and cal_argv(r) == ["--calibration-mic", ""]
    cal = r.src.snapshot()["calibration"]["piano"]
    assert cal["mic"] is None and cal["why"] == "no room mic in show.yaml's senders is labelled for Sonos (study)"
    r.src.stop()


def test_without_the_seam_nothing_is_claimed_and_errors_say_so(lib):
    r = cal_make(lib, seam=False)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:drawing-room")
    assert cal_argv(r) == ["--calibration-mic", "room-mic"] and r.src.snapshot()["mic_claim"] is None
    assert any("can't be claimed" in e and "room-mic" in e for e in r.src.snapshot()["errors"])
    r.src.stop()
    assert not any("claimed" in e for e in r.src.snapshot()["errors"])


def test_a_claim_that_fails_is_tried_again(lib):
    r = cal_make(lib, claim_fails=True)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:drawing-room")
    assert r.claims == [] and any("mic claim failed: RuntimeError: mic busy" in e
                                  for e in r.src.snapshot()["errors"])
    r.fail = False
    r.clock.t += 1.0
    r.src.tick(0.0)
    assert r.claims == [("room-mic", True)] and r.src.snapshot()["mic_claim"] == "room-mic"
    assert not any("claim failed" in e for e in r.src.snapshot()["errors"])
    r.fail = True
    r.src.stop_test("piano")
    assert r.src.snapshot()["mic_claim"] == "room-mic"                          # the release failed: still held
    r.fail = False
    r.clock.t += 1.0
    r.src.tick(0.0)
    assert r.claims[-1] == ("room-mic", False) and r.src.snapshot()["mic_claim"] is None
    r.src.stop()


def test_show_yaml_can_name_the_calibration_mic(lib):
    r = cal_make(lib, calibration_mic={"sonos:drawing-room": "pc22-mic", "pc22": None})
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="sonos:drawing-room")
    assert r.claims == [("pc22-mic", True)] and cal_argv(r) == ["--calibration-mic", "pc22-mic"]
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="pc22")
    assert r.claims[-1] == ("pc22-mic", False) and cal_argv(r) == ["--calibration-mic", ""]
    r.src.stop()
    q = cal_make(lib, calibration_mic="room-mic")
    q.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="pc22")
    assert cal_argv(q) == ["--calibration-mic", "room-mic"] and q.claims == []   # PC22: named, still never claimed
    q.src.stop()


def test_calibration_mic_derivation():
    s = {"senders": {
        "phone": {"hosts": ["h"], "streams": ["phone-test"], "label": "Drawing room"},          # a test stream
        "tv": {"hosts": ["h"], "streams": ["tv-audio"], "label": "Drawing room", "room_sound": False},
        "odd": {"hosts": ["h"], "streams": ["odd-mic"], "label": "Drawing room", "on_demand": True},
        "kiosk": {"hosts": ["h"], "streams": ["room-piano", "keys", "room-mic"], "label": " drawing  ROOM "},
        "drawing_room2": {"hosts": ["h"], "streams": ["dr2-mic"]},
        "bad": {"hosts": [], "streams": ["bad-mic"], "label": "Study"},
        "pc22": {"hosts": ["h"], "port": 6984, "streams": ["pc22-mic"], "label": "PC22"}}}
    rooms = {"drawing-room": "Drawing Room", "two": "Drawing Room 2"}
    health = {"streams": {"keys": {"kind": "midi"}}}
    f = sources.calibration_mic
    assert f(s, "sonos:drawing-room", rooms=rooms, health=health) == ("room-mic",
                                                                       "show.yaml sender kiosk (drawing  ROOM)")
    assert f(s, "sonos:drawing-room", rooms=rooms)[0] == "keys"                 # unclassified: not a known MIDI name
    assert f(s, "sonos:two", rooms=rooms) == ("dr2-mic", "show.yaml sender drawing_room2 (drawing_room2)")   # by name
    assert f(s, "sonos:Drawing-Room", rooms={}, health=health)[0] == "room-mic"   # no room name: the zone key
    assert f(s, "pc22") == ("pc22-mic", "show.yaml sender pc22 (PC22)")
    assert f(s, "sonos:study", rooms={"study": "Study"}) == (
        None, "no room mic in show.yaml's senders is labelled for Sonos (Study)")  # its sender is invalid
    assert f(s, "none") == (None, "nothing is heard") and f(s, None)[0] is None
    assert f(s, "pc22", override="room-mic") == ("room-mic", "show.yaml tests.piano.calibration_mic")
    assert f(s, "pc22", override={"pc22": None}) == (None,
                                                      "show.yaml tests.piano.calibration_mic names no mic for pc22")
    assert f(s, "pc22", override={"sonos:x": "room-mic"})[0] == "pc22-mic"     # not this output: derived
    assert f(s, "pc22", override="bad name!")[0] == "pc22-mic"                 # not a stream name: derived
    assert f({}, "sonos:drawing-room", rooms=rooms)[0] is None
    assert sources.sonos_rooms(sources.SONOS_FILE).get("drawing-room") == "Drawing Room"   # the real file, read only


def test_main_wires_the_sources_mic_claim_seam(monkeypatch, tmp_path):
    from show_service.tests.test_main import _brain_opts, _with_seams, install_brain_fakes, install_fakes
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    install_brain_fakes(monkeypatch)
    seen: list = []

    class Src:
        name, tick_hz = "sources", 1.0

        def __init__(self, ctx, **kw) -> None:
            pass

        def set_mic_claim_fn(self, fn) -> None:
            seen.append(fn)

        def provides(self):
            return {}

        def configure(self, s):
            pass

        def restore(self, s):
            pass

        def start(self):
            pass

        def tick(self, now):
            pass

        def snapshot(self):
            return {}

        def persist(self):
            return None

        def stop(self):
            pass
    m = types.ModuleType("show_service.sources")
    m.attach = lambda ctx, **kw: Src(ctx, **kw)
    monkeypatch.setitem(sys.modules, "show_service.sources", m)
    show = main_mod.Show(_brain_opts(tmp_path))
    show.start()
    try:
        assert len(seen) == 1
        seen[0]("room-mic", True)
        assert {"by": "test", "label": "Test piano", "stream": "room-mic"} in show.mic.state()["claims"]
        seen[0]("room-mic", False)
        assert not any(c.get("by") == "test" for c in show.mic.state()["claims"])
    finally:
        show.stop()


def test_a_stop_for_a_piece_stops_only_that_piece(lib):
    """23 Oct (a check's stop killed the owner's playing piece): stop_test(source, piece=X) stops only while X is
    still the one playing; without piece, as before."""
    instruments(lib)
    (lib / "piano" / "Rag.mid").write_bytes(b"MThd")
    r = make(lib)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    r.src.start_test("piano", "file:Rag.mid", instrument="salamander")        # someone else took over
    assert r.src.stop_test("piano", piece="file:Nocturne.mid") == []          # not ours any more: untouched
    assert r.src.status()["piano"]["piece"] == "file:Rag.mid" and r.src.status()["piano"]["state"] in sources.RUNNING
    assert r.src.stop_test("piano", piece="file:Rag.mid") == ["piano"]
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    assert r.src.stop_test("piano") == ["piano"]                              # unguarded, as before
    r.src.stop()


def test_if_idle_starts_only_while_the_source_is_idle(lib):
    """23 Oct: a script's start (if_idle) never replaces a playing piece: ValueError("busy"); a person's start (no
    if_idle) takes over as before; once stopped, if_idle starts."""
    instruments(lib)
    (lib / "piano" / "Rag.mid").write_bytes(b"MThd")
    r = make(lib)
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander")
    with pytest.raises(ValueError, match="busy"):
        r.src.start_test("piano", "file:Rag.mid", instrument="salamander", if_idle=True)
    assert r.src.status()["piano"]["piece"] == "file:Nocturne.mid"
    r.src.start_test("piano", "file:Rag.mid", instrument="salamander")         # a person's start takes over
    assert r.src.status()["piano"]["piece"] == "file:Rag.mid"
    r.src.stop_test("piano")
    r.popen.procs[-1].rc = 0
    r.clock.t += 1.0
    r.src.tick(0.0)
    assert r.src.status()["piano"]["state"] not in sources.RUNNING
    r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", if_idle=True)
    assert r.src.status()["piano"]["piece"] == "file:Nocturne.mid"
    r.src.stop()


# -- speed (owner, 23 Oct: "midi playback: need a speed/pacing control setting (1X obviously the default)") -------------
SPEED_SENDER = NEW_SENDER + 'ap.add_argument("--speed")\nap.add_argument("--start-s")\n'


def test_speed_and_start_go_to_show_sender_and_its_place_comes_back(lib):
    instruments(lib)
    (lib / "speed_sender.py").write_text(SPEED_SENDER)
    r = make(lib, script="speed_sender.py")
    status = lib / "state" / "test-piano.status.json"
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True, speed=0.75, start_s=30)
    argv = r.popen.argv
    assert argv[argv.index("--speed") + 1] == "0.75" and argv[argv.index("--start-s") + 1] == "30.000"
    assert st["speed"] == 0.75 and st["pos_s"] is None
    status.write_text(json.dumps({"stage": "playing", "progress": 0.3, "pos_s": 41.5, "speed": 0.75}))
    r.health.live = True
    r.clock.t += 0.1
    r.src.tick(0.0)
    s = r.src.status()["piano"]
    assert s["state"] == "playing" and s["pos_s"] == 41.5 and s["speed"] == 0.75
    r.src.start_test("piano", "file:Nocturne.mid", instrument="windows-gs", once=True)       # 1x: no flags
    argv = r.popen.argv
    assert "--speed" not in argv and "--start-s" not in argv and "--status-file" in argv   # where the GS synth is
    with pytest.raises(ValueError, match="bad_speed"):
        r.src.start_test("piano", "file:Nocturne.mid", speed=9)
    r.src.stop()


def test_an_older_show_sender_gets_no_speed(lib):
    instruments(lib)
    r = make(lib)
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True, speed=1.5)
    assert "--speed" not in r.popen.argv and st["speed"] == 1.0                            # said: it plays at 1x
    r.src.stop()


def test_a_device_output_plays_midi_only_and_is_listed(lib):
    """'device:<id>' (piano_live's "This device"): show_sender is told none (PC22 and Sonos quiet), the status keeps
    the device; the devices piano_live has seen are outputs; any device id is taken, a bad one isn't."""
    instruments(lib)
    r = make(lib)
    r.src.devices_fn = lambda: [{"id": "device:ipad-1", "label": "Greg's iPad", "device": True}]
    assert {"id": "device:ipad-1", "label": "Greg's iPad", "device": True} in r.src.choices()["outputs"]
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="device:phone-2", once=True)
    argv = r.popen.argv
    assert argv[argv.index("--output") + 1] == "none" and "--lead-ms" not in argv and st["output"] == "device:phone-2"
    with pytest.raises(ValueError, match="unknown_output"):
        r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="device:../x", once=True)
    r.src.stop()


def test_a_start_naming_no_output_plays_where_the_room_plays(lib):
    """Owner, 24 Oct: 'check that play on this device doesn't get switched off'. A start that names no output (a
    script, an older page) plays on the room's choice (output_fn: the player's) while that's a device or offered,
    never moving the room off This device; an unknown or missing choice falls back to PC22."""
    instruments(lib)
    r = make(lib)
    room = {"out": "device:ipad-1"}
    r.src.output_fn = lambda: room["out"]
    st = r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True)
    argv = r.popen.argv
    assert st["output"] == "device:ipad-1" and argv[argv.index("--output") + 1] == "none"
    room["out"] = "none"                                 # Screens only: an offered output
    assert r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True)["output"] == "none"
    for gone in ("sonos:nowhere", None, 7):              # not offered, none chosen, nonsense: PC22, as before
        room["out"] = gone
        assert r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True)["output"] == "pc22"

    def broken():
        raise RuntimeError("player gone")
    r.src.output_fn = broken
    assert r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", once=True)["output"] == "pc22"
    # a named output still wins
    r.src.output_fn = lambda: "device:ipad-1"
    assert r.src.start_test("piano", "file:Nocturne.mid", instrument="salamander", output="pc22",
                            once=True)["output"] == "pc22"
    r.src.stop()
