"""What stream_service takes from the deployment (smconfig): a screen's fixture, the dev art screens, the music bus,
the files it reads, and the refusals when the machine has no GPU or the deployment has live off (no GPU here)."""
from __future__ import annotations

import json
import os

import pytest
import yaml

import smconfig
from stream_service import inputs, rooms, server, sessions as sessions_mod, workers
from stream_service.plugins.sources import gallery
from stream_service.score import room as score_room
from stream_service.server import MANAGER

from .test_sessions import AUTH, FakeEngine, body, client, create  # noqa: F401 (fixture)


@pytest.fixture
def site(monkeypatch, tmp_path):
    """A deployment of our own: publish-destinations.json and lights/patch.yaml in tmp_path."""
    dests, patch = tmp_path / "publish-destinations.json", tmp_path / "patch.yaml"
    monkeypatch.setattr(inputs, "DESTINATIONS", dests)
    monkeypatch.setattr(inputs, "PATCH", patch)
    monkeypatch.setattr(inputs, "DEFAULT_FIXTURES", {})

    def write(destinations=None, devices=None):
        if destinations is not None:
            dests.write_text(json.dumps(destinations))
        if devices is not None:
            patch.write_text(yaml.safe_dump({"profiles": {}, "devices": devices}))
        inputs._FIXTURE_FILES.clear()
    return write


def test_finsbury_screens_keep_their_fixtures():
    assert inputs.default_fixture("north-screen") == "screen.north"
    assert inputs.default_fixture("south-screen") == "screen.south"
    assert inputs.default_fixture("not-a-screen") is None           # never another screen's


def test_finsbury_devtest_follows_the_north_fixture():
    dev = next(d for d in smconfig.destinations() if d["id"] == "devtest")
    if "fixture" not in dev.get("live", {}):
        pytest.skip("deployments/finsbury/publish-destinations.json: devtest's live.fixture is not set yet")
    assert inputs.default_fixture("devtest") == "screen.north"


def test_fixture_order_live_block_then_patch_then_none(site):
    site([{"id": "tv-a", "live": {"fixture": "screen.left"}}, {"id": "tv-b", "live": {}}, {"id": "phone"}],
         {"screen.a": {"profile": "screen", "screen": "tv-a"}, "screen.b": {"profile": "screen", "screen": "tv-b"},
          "hue.b": {"profile": "hue", "screen": "tv-c"}})
    assert inputs.default_fixture("tv-a") == "screen.left"            # the live block wins over the patch
    assert inputs.default_fixture("tv-b") == "screen.b"               # the patch's screen device
    assert inputs.default_fixture("tv-c") is None                     # a lamp beside a screen is not its fixture
    assert inputs.default_fixture("phone") is None


def test_no_patch_and_no_destinations_mean_no_fixture(site):
    assert inputs.default_fixture("tv-a") is None
    site(devices={"screen.a": {"profile": "screen", "screen": "tv-a"}})
    assert inputs.default_fixture("tv-a") == "screen.a"
    st = inputs.PATCH.stat()
    inputs.PATCH.write_text("devices: [unclosed")                    # broken: the last good copy
    os.utime(inputs.PATCH, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert inputs.default_fixture("tv-a") == "screen.a"


async def test_a_session_takes_the_body_fixture_else_the_derived_one(client, site):  # noqa: F811
    site([{"id": "tv-a", "live": {"fixture": "screen.left"}}], {})
    _, s = await create(client, screen="tv-a")
    assert s.fixture == "screen.left" and s.sampler.fixture_name == "screen.left"
    _, s2 = await create(client, screen="tv-a", fixture="screen.other", idempotency_key="b")
    assert s2.fixture == "screen.other"
    _, s3 = await create(client, screen="nowhere", idempotency_key="c")
    assert s3.fixture is None and s3.sampler.snapshot() is None      # no fixture: follows no light, still runs
    summ = await (await client.get(f"/api/sessions/{s3.id}", headers=AUTH)).json()
    assert summ["fixture"] is None and s3.photon_ms > 0


async def test_no_gpu_refuses_a_create_with_a_clear_error(client, monkeypatch):  # noqa: F811
    from stream_service.renderer import Engine

    import stream_service.gpu as gpu_mod

    mgr = client.app[MANAGER]
    monkeypatch.delattr(mgr, "_devices", raising=False)
    monkeypatch.setattr(gpu_mod, "device_names", lambda: [])          # this machine: zero CUDA devices
    monkeypatch.setattr(sessions_mod, "Engine", Engine)              # the real renderer (needs a GPU)
    r = await client.post("/api/sessions", json=body(), headers=AUTH)
    assert r.status == 400
    res = await r.json()
    assert res["error"] == "no_gpu" and "GPU" in res["detail"] and not mgr.sessions
    assert (await client.get("/healthz")).status == 200               # the service runs on
    r = await client.post("/api/workers/warm", json={"screen": "north-screen"})
    assert r.status == 200 and (await r.json())["spawned"] is False


async def test_an_empty_gpu_probe_is_retried_and_a_later_create_goes_ahead(client, monkeypatch):  # noqa: F811
    """CUDA not ready at the first probe (--warm probes at startup): [] is not kept for the life of the process; it is
    probed again (at most every GPU_REPROBE_S) and a create then goes ahead."""
    import stream_service.gpu as gpu_mod

    mgr = client.app[MANAGER]
    calls = []
    one = [{"index": 0, "name": "fake", "cc": (8, 9), "pci_bus": 1}]

    def probe():
        calls.append(1)
        return [] if len(calls) == 1 else one

    monkeypatch.delattr(mgr, "_devices", raising=False)
    monkeypatch.setattr(gpu_mod, "device_names", probe)
    monkeypatch.setattr(sessions_mod, "Engine", type("GpuEngine", (FakeEngine,), {"needs_gpu": True}))
    r = await client.post("/api/sessions", json=body(), headers=AUTH)
    assert r.status == 400 and (await r.json())["error"] == "no_gpu" and len(calls) == 1
    assert mgr.no_gpu() and len(calls) == 1                           # rate limited: no probe within GPU_REPROBE_S
    mgr._devices_t -= sessions_mod.GPU_REPROBE_S                      # ... GPU_REPROBE_S later
    _, s = await create(client)
    assert len(calls) == 2 and mgr.gpu_names() == one and s.state != "failed"
    assert not mgr.no_gpu() and len(calls) == 2                       # a found GPU is kept


async def test_live_off_refuses_a_create(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(sessions_mod.smconfig, "enabled", lambda cap: cap != "live")
    r = await client.post("/api/sessions", json=body(), headers=AUTH)
    assert r.status == 400 and (await r.json())["error"] == "live_off"


def test_no_gpu_process_starts_and_serves_healthz(tmp_path):
    """python -m stream_service on a machine with no CUDA device (this test venv has no CuPy): it starts and answers
    /healthz."""
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    from stream_service.gpu import device_names
    if device_names():
        pytest.skip("this machine has a CUDA GPU")
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    log = open(tmp_path / "svc.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "stream_service", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=smconfig.REPO_ROOT, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ))
    try:
        t_end = time.time() + 30
        while True:
            assert proc.poll() is None, (tmp_path / "svc.log").read_text()[-2000:]
            try:
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
                assert json.loads(r.read())["ok"] is True
                break
            except OSError:
                assert time.time() < t_end, "no /healthz"
                time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def test_dev_art_screens_are_the_deployments_live_screens(monkeypatch):
    monkeypatch.setattr(server, "DEV_ART_SCREENS", None)
    assert server.dev_art_screens() == ("north-screen", "south-screen", "devtest") == tuple(smconfig.live_screens())
    monkeypatch.setattr(server, "DEV_ART_SCREENS", ("a",))
    assert server.dev_art_screens() == ("a",)


def test_paths_and_addresses_come_from_the_deployment():
    assert workers.DESTINATIONS == smconfig.path("publish-destinations.json") and workers.DESTINATIONS.exists()
    assert rooms.ROOMS_FILE == smconfig.path("rooms.json") and rooms.ROOMS_FILE.exists()
    assert rooms.RoomMap().room_of("north-screen") is not None
    assert workers.screen_live("north-screen").get("profile")
    assert score_room.MUSIC_BUS == "ws://127.0.0.1:8703/v1/show" == smconfig.url("show", "ws") + "/v1/show"
    from stream_service.plugins.sources import live_art
    assert live_art.DEFAULT_ROOM == "http://127.0.0.1:8700"
    assert inputs.PATCH == smconfig.path("lights", "patch.yaml")


def test_gallery_screens_from_the_deployment_and_its_override(monkeypatch, tmp_path):
    monkeypatch.delenv("SM_DESTINATIONS", raising=False)
    monkeypatch.setattr(gallery, "_SCREENS_CACHE", {"mtime": None, "ids": ()})
    assert gallery.screens() == tuple(smconfig.live_screens())
    p = tmp_path / "d.json"
    p.write_text(json.dumps([{"id": "tv", "live": {}}, {"id": "phone"}]))
    monkeypatch.setenv("SM_DESTINATIONS", str(p))
    assert gallery.screens() == ("tv",)


def test_room_proc_music_bus_env_still_wins(monkeypatch):
    monkeypatch.setenv("SM_MUSIC_BUS", "ws://127.0.0.1:1/v1/show")
    assert score_room.RemoteRoom("k").music_bus == "ws://127.0.0.1:1/v1/show"


def test_latency_file_default_is_the_deployments(monkeypatch):
    import stream_service.__main__ as main_mod

    seen = {}

    def make_app(**kw):
        seen.update(kw)
        raise SystemExit(0)
    monkeypatch.setattr("stream_service.server.make_app", make_app)
    monkeypatch.setattr("stream_service.gpu.no_thp_stalls", lambda: None)
    monkeypatch.delenv("SM_STREAM_SWITCHES", raising=False)
    with pytest.raises(SystemExit):
        main_mod.main(["--port", "1"])
    assert seen["latency_file"] == smconfig.path("calibration", "latency.json") and seen["switches_file"] is None
    with pytest.raises(SystemExit):
        main_mod.main(["--switches-file", "/x/sw.json"])
    assert str(seen["switches_file"]) == "/x/sw.json"


def test_gallery_with_no_tv_screen_is_black_not_the_output_root(monkeypatch):
    """No live screen in the deployment: no bucket, so the gallery degrades to black (never indexes output/ itself)."""
    monkeypatch.setattr(gallery, "screens", lambda: ())
    monkeypatch.setattr(gallery, "_deck", lambda screen: pytest.fail(f"a deck for {screen!r}"))
    g = gallery.Gallery()
    ctx = type("Ctx", (), {"screen": "tv", "cp": None, "width": 64, "height": 36, "gpu": 0})()
    g.setup(ctx, {})
    assert g.screen == "" and g.ok is False
