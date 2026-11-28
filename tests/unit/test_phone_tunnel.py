"""routes.phone_door's tunnel keeper: the tunnel outlives a Flask restart and the next Flask keeps it (the address on
the phones and the screens' QR stays the same); one without a known address is replaced; one that ends is started
again. A stand-in cloudflared on its own made-up origin: the real tunnel (the door's port) is never touched."""
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

import smconfig
from routes import phone_door as D

URL = "https://abc-def-ghi.trycloudflare.com"
ORIGIN = "http://127.0.0.1:59917"                     # not the door's: only the stand-ins match it


@pytest.fixture
def rig(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake = bin_ / "cloudflared"                       # prints a quick tunnel's address to stderr, then waits
    fake.write_text(f'#!/bin/sh\necho "INF |  {URL}  |" >&2\nsleep 60\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    log = tmp_path / "phone_tunnel.log"
    monkeypatch.setattr(D, "LOG", log)
    monkeypatch.setattr(D, "_origin", lambda: ORIGIN)
    monkeypatch.setattr(D, "_pid", None)
    monkeypatch.setattr(D, "_url", None)
    monkeypatch.setattr(D, "_clear_join_cache", lambda: None)
    started: list[int] = []
    yield {"log": log, "bin": bin_, "started": started}
    for pid in started:                               # only the stand-ins this test started (their own sessions)
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def _wait(cond, s=5.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_the_tunnel_outlives_the_flask_that_started_it_and_the_next_flask_keeps_it(rig, monkeypatch):
    # "Flask": a process in a session of its own (as start.sh's screen runs it) that starts the tunnel; then its whole
    # process group is hung up and terminated, as `./start.sh restart flask` does
    code = textwrap.dedent(f"""
        import pathlib, sys, time
        from routes import phone_door as D
        D.LOG = pathlib.Path({str(rig['log'])!r})
        D._origin = lambda: {ORIGIN!r}
        print(D._launch(), flush=True)
        time.sleep(60)
    """)
    flask = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                             cwd=str(smconfig.REPO_ROOT), start_new_session=True,
                             env={**os.environ, "PYTHONPATH": str(smconfig.REPO_ROOT)})
    rig["started"].append(flask.pid)
    pid = int(flask.stdout.readline())
    rig["started"].append(pid)
    assert _wait(lambda: D._log_url() == URL)
    os.killpg(flask.pid, signal.SIGHUP)
    os.killpg(flask.pid, signal.SIGTERM)
    flask.wait(5)
    assert _wait(lambda: D._log_url() == URL)
    time.sleep(0.3)
    assert D._alive(pid)                              # its Flask is gone; the tunnel isn't
    assert D._running() == [pid]
    # the next Flask keeps it: no new tunnel, the same address for the phones and the QR
    launched = []
    monkeypatch.setattr(D, "_launch", lambda: launched.append(1) or 0)
    D._adopt()
    D._keep()
    assert launched == [] and D._pid == pid and D.url() == URL


def test_a_running_tunnel_whose_address_isnt_known_is_replaced(rig, monkeypatch):
    rig["log"].write_text("starting...\n")
    killed = []
    monkeypatch.setattr(D, "_running", lambda: [4242])
    monkeypatch.setattr(D, "_kill", killed.append)
    D._adopt()
    assert killed == [4242] and D._pid is None


def test_a_second_running_tunnel_is_ended_and_the_first_kept(rig, monkeypatch):
    rig["log"].write_text(f"INF {URL}\n")
    killed = []
    monkeypatch.setattr(D, "_running", lambda: [4242, 4243])
    monkeypatch.setattr(D, "_kill", killed.append)
    monkeypatch.setattr(D, "_alive", lambda pid: pid == 4242)
    D._adopt()
    assert D._pid == 4242 and killed == [4243] and D.url() == URL


def test_a_tunnel_that_ends_is_started_again_and_its_address_read(rig):
    D._keep()                                         # none yet: one is started
    first = D._pid
    rig["started"].append(first)
    assert _wait(lambda: D._log_url() == URL)
    D._keep()
    assert D.url() == URL
    os.killpg(first, signal.SIGKILL)
    assert _wait(lambda: not D._alive(first))
    assert D.url() is None                            # gone: the QR falls back to the laptop's own address
    D._keep()
    rig["started"].append(D._pid)
    assert D._pid != first and D._alive(D._pid)
    assert _wait(lambda: D._log_url() == URL)
    D._keep()
    assert D.url() == URL


def test_the_qr_is_the_tunnels_address_while_it_is_up(monkeypatch):
    from routes.karaoke import join
    monkeypatch.setattr(D, "url", lambda: URL)
    join.forget()
    assert join.join_url() == URL + join.PAGE
    monkeypatch.setattr(D, "url", lambda: None)
    join.forget()
    assert not (join.join_url() or "").startswith(URL)
    join.forget()
