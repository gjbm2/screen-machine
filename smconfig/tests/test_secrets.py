"""smconfig.secrets: check never prints values; export/import round-trip through a private file; the repo is refused."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import smconfig
from smconfig import secrets as sec

VALUE = "sk-test-NOTAREAL value#1"


@pytest.fixture
def env_file(monkeypatch, tmp_path):
    f = tmp_path / "repo.env"
    f.write_text("SM_DEPLOYMENT=finsbury\nOPENAI_API_KEY=\n")
    monkeypatch.setattr(smconfig, "ENV_FILE", f)
    for k in sec.shareable_keys():
        monkeypatch.delenv(k, raising=False)
    return f


def test_export_import_round_trip(env_file, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", VALUE)
    monkeypatch.setenv("RUNPOD_ID", "abc")
    out = tmp_path / "private" / "keys.env"
    assert sec.export(out) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("RUNPOD_ID")
    assert sec.import_(out) == 0
    got = smconfig._parse_env_text(env_file.read_text())
    assert got["OPENAI_API_KEY"] == VALUE and got["RUNPOD_ID"] == "abc" and got["SM_DEPLOYMENT"] == "finsbury"
    assert env_file.read_text().count("OPENAI_API_KEY=") == 1          # the empty line was replaced, not doubled
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert VALUE not in capsys.readouterr().out


def test_import_keeps_this_machines_value_unless_overwrite(env_file, tmp_path):
    env_file.write_text("RUNPOD_ID=mine\n")
    src = tmp_path / "keys.env"
    src.write_text("RUNPOD_ID=theirs\n")
    sec.import_(src)
    assert smconfig._parse_env_text(env_file.read_text())["RUNPOD_ID"] == "mine"
    sec.import_(src, overwrite=True)
    assert smconfig._parse_env_text(env_file.read_text())["RUNPOD_ID"] == "theirs"


def test_export_refuses_the_repo(env_file):
    assert sec.export(smconfig.REPO_ROOT / "keys.env") == 2
    assert not (smconfig.REPO_ROOT / "keys.env").exists()


def test_check_never_prints_values(env_file, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", VALUE)
    sec.check()
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY" in out and VALUE not in out


def test_every_need_names_a_known_capability():
    assert set(sec.NEEDS) <= set(smconfig.CAPABILITIES)


# --- who else can open them ------------------------------------------------------------------------------------------

@pytest.fixture
def private_layout(env_file, monkeypatch, tmp_path):
    """A repo root with the kiosk's .env, and a secrets folder, all under tmp_path."""
    repo = tmp_path / "repo"
    kiosk_env = repo / "media-server" / "local-scripts" / ".env"
    kiosk_env.parent.mkdir(parents=True)
    kiosk_env.write_text(f'MEDIA_SERVER_USER=someone\nMEDIA_SERVER_PASSWORD="{VALUE}"\n')
    secrets_dir = tmp_path / "config" / "screen-machine"
    (secrets_dir / "rf").mkdir(parents=True)
    (secrets_dir / "rf" / "bloc_hub.json").write_text('{"key": "NOTAREALKEY12345"}')
    monkeypatch.setattr(sec, "REPO_ROOT", repo)
    monkeypatch.setattr(smconfig, "SECRETS_DIR", secrets_dir)
    return {"env": env_file, "kiosk_env": kiosk_env, "dir": secrets_dir, "key": secrets_dir / "rf" / "bloc_hub.json"}


def _modes(lay, env, kiosk, folder, key):
    os.chmod(lay["env"], env)
    os.chmod(lay["kiosk_env"], kiosk)
    os.chmod(lay["dir"], folder)
    os.chmod(lay["key"], key)


def test_private_paths_name_env_kiosk_env_and_folder(private_layout):
    got = dict(sec.private_paths())
    assert got == {private_layout["env"]: 0o600, private_layout["kiosk_env"]: 0o600, private_layout["dir"]: 0o700}


def test_permission_warnings_name_paths_only(private_layout):
    _modes(private_layout, 0o644, 0o644, 0o755, 0o644)
    warns = sec.permission_warnings()
    assert len(warns) == 4
    assert any("media-server/local-scripts/.env is 0644" in w and "chmod 600" in w for w in warns)
    assert any(str(private_layout["dir"]) in w and "0755" in w and "chmod 700" in w for w in warns)
    assert any("bloc_hub.json is 0644" in w for w in warns)
    assert not any(VALUE in w or "NOTAREALKEY" in w for w in warns)


def test_no_permission_warnings_when_private(private_layout):
    _modes(private_layout, 0o600, 0o600, 0o700, 0o600)
    assert sec.permission_warnings() == []


def test_check_warns_but_exit_code_is_the_same(private_layout, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", VALUE)
    _modes(private_layout, 0o600, 0o600, 0o700, 0o600)
    quiet = sec.check()
    assert "warning:" not in capsys.readouterr().out
    _modes(private_layout, 0o644, 0o640, 0o755, 0o604)
    loud = sec.check()
    out = capsys.readouterr().out
    assert loud == quiet
    assert out.count("warning:") == 4 and VALUE not in out and "NOTAREALKEY" not in out


def test_kiosk_password_is_read_from_its_own_file(private_layout, monkeypatch):
    need = next(n for n in sec.NEEDS["kiosk_admin"] if n.name == "MEDIA_SERVER_PASSWORD")
    assert need.env_file == "media-server/local-scripts/.env"
    assert sec._is_set(need, {})
    private_layout["kiosk_env"].write_text("MEDIA_SERVER_USER=someone\n")
    assert not sec._is_set(need, {"MEDIA_SERVER_PASSWORD": "in .env, which admin_api.py doesn't read"})


def test_machine_own_keys_never_exported():
    keys = sec.shareable_keys()
    assert "MEDIA_SERVER_PASSWORD" not in keys and "NGROK_DOMAIN" not in keys and "ADMIN_API_TOKEN" not in keys


def test_inventory_has_the_site_secrets():
    names = {(cap, n.name) for cap, ns in sec.NEEDS.items() for n in ns}
    assert {("kiosk_admin", "MEDIA_SERVER_PASSWORD"), ("blinds", "rf/bloc_hub.json"), ("room", "room_sensors.json"),
            ("ngrok", "NGROK_DOMAIN")} <= names


def test_every_env_key_is_in_env_example():
    example = smconfig._parse_env_text((smconfig.REPO_ROOT / ".env.example").read_text())
    missing = {n.name for ns in sec.NEEDS.values() for n in ns if n.kind == "env" and not n.env_file} - set(example)
    assert not missing


# --- export where 0600 doesn't hold ----------------------------------------------------------------------------------

def test_export_says_0600_on_a_normal_disk(env_file, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sec, "_mount_fstype", lambda p: "ext4")
    monkeypatch.setenv("RUNPOD_ID", "abc")
    assert sec.export(tmp_path / "keys.env") == 0
    out = capsys.readouterr().out
    assert "(0600)" in out and "warning" not in out


def test_export_warns_on_a_windows_drive_and_still_writes(env_file, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sec, "_mount_fstype", lambda p: "drvfs")
    monkeypatch.setenv("OPENAI_API_KEY", VALUE)
    out_file = tmp_path / "keys.env"
    assert sec.export(out_file) == 0
    out = capsys.readouterr().out
    assert "(0600)" not in out and "warning:" in out and "gpg -c" in out and "cloud" in out
    assert VALUE not in out
    assert "OPENAI_API_KEY=" in out_file.read_text()


def test_export_warns_when_chmod_does_not_take(env_file, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sec, "_mount_fstype", lambda p: "ext4")
    monkeypatch.setenv("RUNPOD_ID", "abc")
    out_file = tmp_path / "keys.env"
    out_file.write_text("")
    os.chmod(out_file, 0o644)
    monkeypatch.setattr(sec.os, "chmod", lambda *a, **k: None)      # a file system that ignores chmod
    assert sec.export(out_file) == 0
    out = capsys.readouterr().out
    assert "(0600)" not in out and "warning:" in out


def test_mnt_drive_letter_counts_as_windows(monkeypatch):
    monkeypatch.setattr(sec, "_mount_fstype", lambda p: "")
    assert sec._unprotected(Path("/mnt/c/Users/someone/OneDrive/Private/keys.env"))
    assert sec._WINDOWS_DRIVE.match("/mnt/d") and not sec._WINDOWS_DRIVE.match("/mnt/data/keys.env")
