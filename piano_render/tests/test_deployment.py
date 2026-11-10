"""What piano_render takes from the deployment (smconfig): file paths, service addresses, the host paths (Windows
Python, the audition folder) and the refusals when one is unset or the deployment has no Sonos. No sound, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smconfig  # noqa: E402

pytest.importorskip("soundfile")


def test_files_and_addresses_are_the_deployments():
    from piano_render import calibrate, session, sonos
    assert sonos.SONOS_FILE == smconfig.path("room", "sonos.json")
    assert sonos.LATENCY_FILE == calibrate.ROOM_LATENCY_FILE == smconfig.path("calibration", "latency.json")
    assert sonos.ROOM_URL == "http://127.0.0.1:8700" == smconfig.url("room")
    assert session.SHOW_URL == calibrate.SHOW_URL == "http://127.0.0.1:8703" == smconfig.url("show")


def test_host_paths_are_the_deployments():
    from piano_render import audition, pc22
    assert pc22.PY == "/mnt/c/Windows/py.exe" == str(smconfig.deployment().host_path("windows_python"))
    assert audition.AUDITION_DIR == smconfig.deployment().host_path("piano_audition_dir")


def test_no_windows_python_is_a_clear_refusal(monkeypatch):
    from piano_render import pc22
    monkeypatch.setattr(pc22, "PY", None)
    assert pc22.available() is False
    out = pc22.PC22Output(py=None)
    with pytest.raises(pc22.PC22Error, match="windows_python"):
        out.start("/nonexistent.wav")


def test_no_audition_folder_is_a_clear_refusal(monkeypatch, tmp_path):
    from piano_render import audition
    monkeypatch.setattr(audition, "AUDITION_DIR", None)
    with pytest.raises(ValueError, match="piano_audition_dir"):
        audition.run(["x"], work_dir=tmp_path, registry=object())


def test_sonos_off_is_a_clear_refusal(monkeypatch, tmp_path):
    from piano_render import sonos
    monkeypatch.setattr(sonos.smconfig, "enabled", lambda cap: cap != "sonos")
    with pytest.raises(sonos.SonosError, match="no Sonos"):
        sonos.SonosOutput("any", sonos_file=tmp_path / "sonos.json")


def test_no_sonos_file_means_no_zones(tmp_path):
    from piano_render import calibrate, sonos
    with pytest.raises(sonos.SonosError, match="no Sonos zone"):
        sonos.SonosOutput("any", sonos_file=tmp_path / "missing.json")
    assert sonos.start_ms_for("any", tmp_path / "missing.json") == sonos.START_MS_DEFAULT
    assert calibrate._sonos_outputs() == [f"sonos:{z}" for z in
                                          smconfig.read_data(smconfig.path("room", "sonos.json"))["zones"]]


def test_the_calibration_mic_comes_from_the_deployments_senders(monkeypatch, tmp_path):
    from piano_render import calibrate, sonos
    assert calibrate.default_mic("pc22") == "pc22-mic"                 # finsbury: PC22's own sender
    assert calibrate.default_mic("sonos:drawing-room") == "room-mic"   # the sender labelled "Drawing room"
    assert calibrate.default_mic("sonos:kitchen") == ""                 # no sender hears it: no mic check
    show = tmp_path / "show.yaml"
    show.write_text("senders:\n  den:\n    hosts: [h]\n    port: 1\n    streams: [den-midi, den-mic]\n"
                    "situations:\n  midi:\n    streams: [den-midi]\n")
    real_path = smconfig.path
    monkeypatch.setattr(calibrate.smconfig, "path", lambda *p: show if p == ("show.yaml",) else real_path(*p))
    monkeypatch.setattr(sonos, "SONOS_FILE", tmp_path / "none.json")
    assert calibrate.default_mic("sonos:den") == "den-mic"             # not the MIDI stream
    assert calibrate.default_mic("pc22") == ""                          # this site has no PC22 sender
    monkeypatch.setattr(calibrate, "MIC_FOR", {"pc22": "x-mic"})
    assert calibrate.default_mic("pc22") == "x-mic"
