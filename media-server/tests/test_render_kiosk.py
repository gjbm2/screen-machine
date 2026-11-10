"""media-server/render_kiosk.py: the kiosk's remote scripts rendered from the deployment's kiosk block.

The owner's hard rule: the kiosk/TV page URLs never change. So finsbury's rendered files must be byte-identical to the
files before they became templates (render_kiosk.BASELINE_REF)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("render_kiosk", HERE.parent / "render_kiosk.py")
rk = importlib.util.module_from_spec(_spec)
sys.modules["render_kiosk"] = rk
_spec.loader.exec_module(rk)

EXPECTED = {"deadman.py", "fix-lightsensor-ip.sh", "kiosk-loop.sh", "wayland/kiosk-wayland.sh", "webview-manager.sh",
            "kiosk.service", "kiosk-x11.service", "kiosk-wait-start.sh",                     # the kiosk login
            "restart-lightsensor.sh", "clean-restart-lightsensor.sh", "fix-lightsensor-bug.sh"}


def _have_ref(ref: str) -> bool:
    r = subprocess.run(["git", "-C", str(rk.REPO), "cat-file", "-e", f"{ref}^{{commit}}"], capture_output=True)
    return r.returncode == 0


def test_templated_files_are_the_site_specific_ones():
    assert set(rk.templated_files()) == EXPECTED


def test_finsbury_renders_byte_identical_to_baseline():
    if not _have_ref(rk.BASELINE_REF):
        pytest.skip(f"{rk.BASELINE_REF} not in this clone")
    assert rk.check(rk.BASELINE_REF, rk.values("finsbury")) == []


def test_finsbury_values():
    v = rk.values("finsbury")
    assert v["KIOSK_BASE_URL"] == "http://95.141.21.170:8000"
    assert v["KIOSK_BASE_HOST"] == "95.141.21.170"
    assert v["KIOSK_SCREEN_NORTH"] == "north-screen"
    assert v["KIOSK_SCREEN_SOUTH"] == "south-screen"


def test_render_dir_leaves_no_tokens(tmp_path):
    rendered = rk.render_dir(tmp_path, rk.values("finsbury"))
    assert set(rendered) == EXPECTED
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert b"@@KIOSK_" not in p.read_bytes(), p
    src = rk.REMOTE_DIR / "kiosk-loop.sh"
    assert (tmp_path / "kiosk-loop.sh").stat().st_mode == src.stat().st_mode


def test_deployment_without_kiosk_is_refused():
    with pytest.raises(rk.RenderError, match="no kiosk block"):
        rk.values("example")


def test_missing_value_is_refused():
    with pytest.raises(rk.RenderError, match="KIOSK_SCREEN_EAST"):
        rk.render_text('URL="@@KIOSK_BASE_URL@@/display/@@KIOSK_SCREEN_EAST@@"', {"KIOSK_BASE_URL": "http://x"}, "t")


def test_check_cli_exit_codes(capsys):
    if not _have_ref(rk.BASELINE_REF):
        pytest.skip(f"{rk.BASELINE_REF} not in this clone")
    assert rk.main(["--deployment", "finsbury", "check"]) == 0
    assert rk.main(["--deployment", "example", "check"]) == 2
