"""scripts/migrate_deployment.py: the machine step's venvs and their PyYAML lines (fake venvs under a fake HOME)."""
from __future__ import annotations

import importlib.util
import stat

import pytest

import smconfig

_spec = importlib.util.spec_from_file_location("migrate_deployment",
                                               smconfig.REPO_ROOT / "scripts" / "migrate_deployment.py")
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


def _venv(home, name, has_yaml: bool, pip_ok: bool = True):
    """A fake ~/.venvs/<name>/bin/python: `-c "import yaml"` succeeds when has_yaml; `-m pip` succeeds when pip_ok
    (and then records the install), else fails the way pip does with no network."""
    b = home / ".venvs" / name / "bin"
    b.mkdir(parents=True)
    py = b / "python"
    py.write_text(f"""#!/bin/sh
if [ "$1" = "-c" ]; then [ -f "{b}/yaml" ] || [ {int(has_yaml)} = 1 ]; exit $?; fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  if [ {int(pip_ok)} = 1 ]; then touch "{b}/yaml"; exit 0; fi
  echo "WARNING: Retrying after connection broken" >&2
  echo "ERROR: No matching distribution found for PyYAML" >&2
  exit 1
fi
exit 2
""")
    py.chmod(py.stat().st_mode | stat.S_IXUSR)
    return py


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_venvs_are_every_sm_venv_with_a_python(home):
    assert migrate._venvs() == []                                   # no ~/.venvs at all
    _venv(home, "sm-stream", True)
    _venv(home, "sm-roundtrip", False)                              # the audio calibration tools' venv
    (home / ".venvs" / "sm-empty").mkdir()                          # no python: not a venv
    (home / ".venvs" / "other").mkdir()
    (home / ".venvs" / "sm-musiclib.lock").write_text("")
    assert migrate._venvs() == ["sm-roundtrip", "sm-stream"]


def _lines(rep):
    return [ln for ln in rep.lines if "PyYAML in" in ln]


def test_one_line_per_venv_plan_and_apply(home):
    _venv(home, "sm-dot", False)
    _venv(home, "sm-show", True)
    rep = migrate.Report()
    for n in migrate._venvs():
        migrate._pyyaml(n, False, rep)
    assert _lines(rep) == [
        f"  do   PyYAML in ~/.venvs/sm-dot: missing; --apply installs it (by hand: "
        f"{home}/.venvs/sm-dot/bin/python -m pip install PyYAML)",
        "  ok   PyYAML in ~/.venvs/sm-show: there"]
    assert rep.problems == 0
    rep = migrate.Report()
    for n in migrate._venvs():
        migrate._pyyaml(n, True, rep)
    assert _lines(rep) == ["  ok   PyYAML in ~/.venvs/sm-dot: installed now", "  ok   PyYAML in ~/.venvs/sm-show: there"]
    assert len(rep.lines) == 2 and rep.problems == 0


def test_a_failed_install_names_the_command_and_the_run_carries_on(home):
    _venv(home, "sm-a", False, pip_ok=False)
    _venv(home, "sm-b", False, pip_ok=True)
    rep = migrate.Report()
    for n in migrate._venvs():
        migrate._pyyaml(n, True, rep)
    a, b = _lines(rep)
    assert a.startswith("  FAIL PyYAML in ~/.venvs/sm-a: MISSING, and pip could not install it "
                        "(ERROR: No matching distribution found for PyYAML)")
    assert a.endswith(f"Run by hand: {home}/.venvs/sm-a/bin/python -m pip install PyYAML")
    assert b == "  ok   PyYAML in ~/.venvs/sm-b: installed now"          # the next venv is still done
    assert rep.problems == 1


def test_machine_step_says_when_there_are_no_venvs(home, monkeypatch, tmp_path):
    monkeypatch.setattr(smconfig, "ENV_FILE", tmp_path / "absent.env")
    rep = migrate.Report()
    migrate.step_machine(smconfig.load("finsbury"), False, rep)
    assert any("no ~/.venvs/sm-* here" in ln for ln in rep.lines)
