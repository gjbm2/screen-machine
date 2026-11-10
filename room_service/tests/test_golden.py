"""The golden baselines (multi-room plan, step 1): what room_service resolves from each shipped deployment's real
files, recorded on the tree before any multi-room change. Until a room sets "own_modes" or "show": false, every later
step must reproduce them exactly; only the finsbury Lab opt-in (plan step 8) re-records finsbury, on purpose.

Each deployment runs in its own process (golden_probe.py: SM_DEPLOYMENT=<id>, so every module-level site path is that
deployment's), with fake stream_service and Flask and every device a placeholder. The parts are compared one by one:
status (ages, epochs, times scrubbed), GET /api/v1/modes, /room/describe, the config revision, the derived
'<room>:*' groups, POST /room/plan for every mode, and "switches": every mode switched to in turn (each PUT's 202
body, the mode.transition events, the event types, the status after, the state files and sensor_mask.json and
brightness.json as written). "switches" was added after the first recording; it was checked equal on HEAD (df3be8ab
plus the README commits) and on the working tree before it was recorded.

To re-record on purpose: SM_GOLDEN_RECORD=1 pytest room_service/tests/test_golden.py, then review the diff in git.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).resolve().parent / "golden"
DEPLOYMENTS = ("finsbury", "laptop", "example")
PARTS = ("status", "modes", "describe", "revision", "groups", "plan", "switches")


# The deployment files the probe reads. A golden that fails while one of these changed since it was recorded (the Room
# page's mode editor writes modes.json, Settings › Lights writes room/rf/) is new input, not a regression: the message
# says so, and re-recording is right once the code is known unchanged.
INPUTS = ("rooms.json", "publish-destinations.json", "intensity.json", "room/room.yaml", "room/modes.json",
          "room/tv.json", "room/sonos.json", "lights/patch.yaml")


def inputs(dep: str) -> dict:
    import hashlib

    import smconfig
    d = smconfig.load(dep)
    rels = list(INPUTS) + sorted(f"room/rf/{p.name}" for p in d.path("room", "rf").glob("*.json"))
    rels += sorted(f"room/{p.name}" for p in d.path("room").glob("modes.*.json"))   # own_modes rooms' files
    return {rel: (hashlib.sha1(d.path(*rel.split("/")).read_bytes()).hexdigest()
                  if d.path(*rel.split("/")).is_file() else None) for rel in rels}


def golden_path(dep: str, part: str) -> Path:
    return GOLDEN / f"{dep}_{part}.json"


def probe(dep: str) -> dict:
    env = {**os.environ, "SM_DEPLOYMENT": dep}
    r = subprocess.run([sys.executable, "-m", "room_service.tests.golden_probe"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, f"golden_probe {dep} failed:\n{r.stderr[-4000:]}"
    return json.loads(r.stdout)


@pytest.mark.timeout(300)                   # the switches part waits out every mode's transition (~40 s on finsbury)
@pytest.mark.parametrize("dep", DEPLOYMENTS)
def test_room_service_matches_its_golden(dep):
    got = probe(dep)
    assert got["deployment"] == dep
    if os.environ.get("SM_GOLDEN_RECORD") == "1":
        GOLDEN.mkdir(exist_ok=True)
        for part in PARTS:
            golden_path(dep, part).write_text(json.dumps(got[part], indent=1, sort_keys=True) + "\n")
        golden_path(dep, "inputs").write_text(json.dumps(inputs(dep), indent=1, sort_keys=True) + "\n")
        pytest.skip(f"recorded room_service/tests/golden/{dep}_*.json")
    was, now = json.loads(golden_path(dep, "inputs").read_text()), inputs(dep)
    changed = sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))
    for part in PARTS:
        want = json.loads(golden_path(dep, part).read_text())
        assert got[part] == want, (f"{dep} {part} differs from room_service/tests/golden/{dep}_{part}.json" + (
            f" (but deployments/{dep}/ {', '.join(changed)} changed since it was recorded: new input, re-record "
            "if the code is unchanged)" if changed else ""))


def test_finsbury_golden_is_the_shared_modes_shape():
    """What the plan says PC22 keeps until the Lab opts in: one site zone, today's members, no 'zones' key. Once the
    Lab has "own_modes": true (plan step 8, re-recorded on purpose): the Lab zone, its own members, no live-test in
    the site's modes."""
    st = json.loads(golden_path("finsbury", "status").read_text())
    subs = st["subsystems"]
    assert [r["id"] for r in st["rooms"]] == ["drawing-room", "lab"]
    if not any(r.get("own_modes") for r in st["rooms"]):
        assert sorted(subs["sensor_mask"]["members"]) == ["main"]
        assert sorted(subs["brightness"]["members"]) == ["lamps", "screens"]
        assert sorted(subs["show"]["members"]) == ["ai", "main", "mic"]
        assert "zones" not in st
        assert all("own_modes" not in r and "show" not in r for r in st["rooms"])
        return
    assert [r.get("own_modes") for r in st["rooms"]] == [None, True] and all("show" not in r for r in st["rooms"])
    assert [z["room"] for z in st["zones"]] == ["lab"] and st["zones"][0]["screens"] == ["devtest"]
    assert sorted(subs["sensor_mask"]["members"]) == ["lab", "main"]
    assert sorted(subs["brightness"]["members"]) == ["lab.screens", "lamps", "screens"]
    assert sorted(subs["show"]["members"]) == ["ai", "lab.ai", "lab.mic", "main", "mic"]
    assert st["mode"]["rooms_error"] is None
    assert "live-test" not in json.loads(golden_path("finsbury", "modes").read_text())["modes"]
