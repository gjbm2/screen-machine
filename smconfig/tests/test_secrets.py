"""smconfig.secrets: check never prints values; export/import round-trip through a private file; the repo is refused."""
from __future__ import annotations

import stat

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
