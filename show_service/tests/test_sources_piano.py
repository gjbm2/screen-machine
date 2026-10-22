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
