#!/usr/bin/env python3
"""finsbury's Lab gets its own modes (multi-room step 8): edits PC22's own files in place, so nothing PC22 changed in
rooms.json or modes.json is lost. Run from the repo root (APPLY.md has the whole procedure and the checks):

    .venv/bin/python docs/multiroom/finsbury-lab/apply.py              # dry run: says what it would change
    .venv/bin/python docs/multiroom/finsbury-lab/apply.py --apply      # do it (backs the two files up first)
    .venv/bin/python docs/multiroom/finsbury-lab/apply.py --rollback   # put the backed-up files back, remove the Lab's

Changes: rooms.json `lab` gains "own_modes": true; modes.json loses its `live-test` mode; room/modes.lab.json (this
folder's) is added: the Lab's Normal and Live test.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEP = REPO / "deployments" / "finsbury"
ROOMS, MODES, LAB = DEP / "rooms.json", DEP / "room" / "modes.json", DEP / "room" / "modes.lab.json"
HERE_LAB = Path(__file__).resolve().parent / "modes.lab.json"
BACKUP = Path("~/.local/state/screen-machine/step8-rollback").expanduser()


def _dump(d) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def plan() -> tuple[dict, dict, list[str]]:
    rooms, modes = json.loads(ROOMS.read_text()), json.loads(MODES.read_text())
    problems = []
    lab = rooms.get("lab")
    if not isinstance(lab, dict) or "devtest" not in (lab.get("screens") or []):
        problems.append("rooms.json has no 'lab' room holding devtest")
    elif lab.get("own_modes") is True:
        problems.append("rooms.json: lab already has own_modes (applied before?)")
    if "live-test" not in (modes.get("modes") or {}):
        problems.append("modes.json has no 'live-test' mode")
    if modes.get("default") == "live-test":
        problems.append("modes.json's default is live-test: change it first")
    if LAB.exists() and LAB.read_text() != HERE_LAB.read_text():
        problems.append(f"{LAB.relative_to(REPO)} exists and differs from this folder's")
    if problems:
        return rooms, modes, problems
    lab["own_modes"] = True
    del modes["modes"]["live-test"]
    return rooms, modes, []


def main(argv: list[str]) -> int:
    if "--rollback" in argv:
        for f, dst in (("rooms.json", ROOMS), ("modes.json", MODES)):
            src = BACKUP / f
            if not src.exists():
                print(f"no backup {src}: nothing to roll back", file=sys.stderr)
                return 1
            shutil.copy2(src, dst)
        LAB.unlink(missing_ok=True)
        print(f"restored rooms.json and modes.json from {BACKUP}; removed {LAB.relative_to(REPO)}")
        return _check()
    rooms, modes, problems = plan()
    if problems:
        print("refusing:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print("would change:" if "--apply" not in argv else "changing:")
    print("  rooms.json: lab gains \"own_modes\": true")
    print("  room/modes.json: the live-test mode moves out (to the Lab)")
    print("  room/modes.lab.json: added (Normal, Live test)")
    if "--apply" not in argv:
        print("dry run: add --apply")
        return 0
    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    for src in (ROOMS, MODES):
        shutil.copy2(src, BACKUP / src.name)
        shutil.copy2(src, BACKUP / f"{src.name}.{stamp}")
    ROOMS.write_text(_dump(rooms))
    MODES.write_text(_dump(modes))
    shutil.copy2(HERE_LAB, LAB)
    print(f"done; the old files are in {BACKUP}")
    return _check()


def _check() -> int:
    r = subprocess.run([sys.executable, "-m", "smconfig", "--deployment", "finsbury", "check"], cwd=REPO)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
