"""scripts/migrate_deployment.py makes the secrets this user's only: .env and the kiosk's .env 0600, the secrets folder
0700 (made 0700 when it creates it). The plan says what it would change and changes nothing."""
from __future__ import annotations

import importlib.util
import os
import stat

import pytest

import smconfig
from smconfig import secrets as sec

_spec = importlib.util.spec_from_file_location("migrate_deployment",
                                               smconfig.REPO_ROOT / "scripts" / "migrate_deployment.py")
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)


def _mode(p):
    return stat.S_IMODE(p.stat().st_mode)


@pytest.fixture
def layout(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    env = repo / ".env"
    kiosk_env = repo / "media-server" / "local-scripts" / ".env"
    kiosk_env.parent.mkdir(parents=True)
    env.write_text("SM_DEPLOYMENT=finsbury\n")
    kiosk_env.write_text("MEDIA_SERVER_PASSWORD=x\n")
    secrets_dir = tmp_path / "home" / ".config" / "screen-machine"
    secrets_dir.mkdir(parents=True)
    (secrets_dir / "dot.json").write_text("{}")
    os.chmod(env, 0o644)
    os.chmod(kiosk_env, 0o644)
    os.chmod(secrets_dir, 0o755)
    os.chmod(secrets_dir / "dot.json", 0o600)
    monkeypatch.setattr(smconfig, "ENV_FILE", env)
    monkeypatch.setattr(smconfig, "SECRETS_DIR", secrets_dir)
    monkeypatch.setattr(sec, "REPO_ROOT", repo)
    monkeypatch.setattr(md, "REPO", repo)
    return {"repo": repo, "env": env, "kiosk_env": kiosk_env, "dir": secrets_dir}


def test_plan_reports_and_changes_nothing(layout, capsys):
    rep = md.Report()
    md.step_private(False, rep)
    dos = [ln for ln in rep.lines if ln.startswith("  do")]
    assert len(dos) == 3
    assert any("chmod 600 .env (it is 0644" in ln for ln in dos)
    assert any("chmod 600 media-server/local-scripts/.env (it is 0644" in ln for ln in dos)
    assert any("chmod 700" in ln and "it is 0755" in ln for ln in dos)
    assert (_mode(layout["env"]), _mode(layout["kiosk_env"]), _mode(layout["dir"])) == (0o644, 0o644, 0o755)
    assert rep.problems == 0


def test_apply_narrows_and_says_what_it_was(layout):
    rep = md.Report()
    md.step_private(True, rep)
    assert (_mode(layout["env"]), _mode(layout["kiosk_env"]), _mode(layout["dir"])) == (0o600, 0o600, 0o700)
    assert sum("was 0644" in ln for ln in rep.lines) == 2 and any("was 0755" in ln for ln in rep.lines)
    again = md.Report()
    md.step_private(True, again)
    assert not [ln for ln in again.lines if ln.startswith("  do")]
    assert sum(ln.startswith("  ok") for ln in again.lines) == 3


def test_never_widens_and_skips_what_is_absent(layout):
    os.chmod(layout["env"], 0o400)
    layout["kiosk_env"].unlink()
    rep = md.Report()
    md.step_private(True, rep)
    assert _mode(layout["env"]) == 0o400
    assert not any("local-scripts" in ln for ln in rep.lines)


def test_token_move_makes_the_folder_0700(layout, monkeypatch, tmp_path):
    fresh = tmp_path / "fresh" / ".config" / "screen-machine"
    monkeypatch.setattr(smconfig, "SECRETS_DIR", fresh)
    monkeypatch.setattr(md, "SECRET_MOVES", {"x-screen-token.txt": "samsung/x-screen-token.txt"})
    (layout["repo"] / "x-screen-token.txt").write_text("NOTAREALTOKEN")
    rep = md.Report()
    md.step_secrets(True, rep)
    tok = fresh / "samsung" / "x-screen-token.txt"
    assert tok.read_text() == "NOTAREALTOKEN" and not (layout["repo"] / "x-screen-token.txt").exists()
    assert (_mode(fresh), _mode(fresh / "samsung"), _mode(tok)) == (0o700, 0o700, 0o600)
    assert (_mode(layout["env"]), _mode(layout["kiosk_env"])) == (0o600, 0o600)
    assert rep.problems == 0
