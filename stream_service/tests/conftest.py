"""Test configuration.  GPU/encoder and browser tests are opt-in:

  SM_GPU_TESTS=1      run tests that start the service and encode on a GPU (default GPU 1: SM_TEST_GPU)
  SM_BROWSER_TESTS=1  also run the headless Chrome playback test
"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.request

import pytest

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")

REPO = pathlib.Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("SM_TEST_PORT", "8082"))
BASE = f"http://127.0.0.1:{PORT}"
GPU = int(os.environ.get("SM_TEST_GPU", "1"))


def pytest_collection_modifyitems(config, items):
    gpu = os.environ.get("SM_GPU_TESTS") == "1"
    browser = os.environ.get("SM_BROWSER_TESTS") == "1"
    for item in items:
        if "gpu" in item.keywords and not gpu:
            item.add_marker(pytest.mark.skip(reason="set SM_GPU_TESTS=1 to run GPU/encoder tests"))
        if "browser" in item.keywords and not (gpu and browser):
            item.add_marker(pytest.mark.skip(reason="set SM_GPU_TESTS=1 SM_BROWSER_TESTS=1"))


@pytest.fixture(autouse=True)
def _no_harness_data(monkeypatch, tmp_path_factory):
    """Hermetic vetted pool: no harness sheets / costs and no calibration from ~/.cache/sm-fxtest (they change
    nightly).  Tests that need them point stream_service.library at their own files."""
    from stream_service import library

    root = tmp_path_factory.mktemp("fxtest-empty")
    monkeypatch.setattr(library, "FXTEST_OUT", root / "out")
    monkeypatch.setattr(library, "CALIBRATION_FILE", root / "calibration.json")


@pytest.fixture(scope="session")
def service(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("svc")
    log = open(tmp / "service.log", "w")
    env = {**os.environ, "CUDA_DEVICE_ORDER": "PCI_BUS_ID"}
    proc = subprocess.Popen([sys.executable, "-m", "stream_service", "--host", "127.0.0.1", "--port", str(PORT)],
                            cwd=REPO, stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        t_end = time.time() + 20
        while time.time() < t_end:
            if proc.poll() is not None:
                raise RuntimeError(f"service exited rc={proc.returncode}: {(tmp / 'service.log').read_text()[-2000:]}")
            try:
                urllib.request.urlopen(f"{BASE}/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("service did not come up")
        yield {"log": tmp / "service.log", "proc": proc}
    finally:
        proc.send_signal(signal.SIGINT)   # aiohttp shuts down gracefully: sessions end, ffmpeg exits
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
