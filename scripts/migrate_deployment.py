#!/usr/bin/env python3
"""Move a machine that ran the old (pre-deployments/) layout onto deployments/<id>/, and prove nothing changed.

Run it from the repo root, with the repo venv, right after `git pull` brought in the deployments/ layout:

    .venv/bin/python scripts/migrate_deployment.py --deployment finsbury            # plan only: what it would do
    .venv/bin/python scripts/migrate_deployment.py --deployment finsbury --apply    # do it
    .venv/bin/python scripts/migrate_deployment.py --deployment finsbury --verify-live   # after the restart

What --apply does (deployments/README.md, "Migrating PC22", has the whole procedure):
  1. .env: adds SM_DEPLOYMENT=<id> (refuses if it names another deployment).
  2. Leftovers: any file still in an old location (an untracked device file the app wrote, e.g. a new
     config/room/rf/<id>.json) moves to its new home; an identical duplicate is removed; a different one is a
     conflict and stops the run (nothing is overwritten).
  3. Secrets: the Samsung pairing tokens move from the repo root (or, once git removed them, from git history)
     to ~/.config/screen-machine/samsung/ (0600). Then the secrets become this user's only: .env and the kiosk's
     media-server/local-scripts/.env 0600, ~/.config/screen-machine/ 0700 (smconfig.secrets.private_paths). Every
     reader runs as this user (Spectre and Caddy use wsl.exe as the distro's default user), so nothing else needs them.
  4. Proof: every moved file is compared with its old content at the pre-pull commit (ORIG_HEAD, or --from-rev);
     config/room/show.yaml is compared after layering (shared file + deployments/<id>/show.yaml == the old file).
  5. `python -m smconfig check` must pass; the effective config is saved to logs/smconfig-<id>-<time>.json.
  6. Outside the repo: reports any Spectre module or boot script that still names an old path.
  7. This machine: the .env settings the service flags use (${VAR}) are set; every ~/.venvs/sm-* has PyYAML
     (installs it when missing; one "PyYAML in ~/.venvs/<name>" line each, and a failed install gives the pip
     command to run by hand); with ngrok on, .env gets NGROK_DOMAIN (the old start.sh default) if it lacks it.
--verify-live checks the running services answer as the deployment says (health, screens, rooms, capabilities).

Exit codes: 0 ok, 1 a problem (the report says which), 2 bad usage.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import filecmp
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import smconfig  # noqa: E402
from smconfig import check as smcheck  # noqa: E402
from smconfig import secrets as smsecrets  # noqa: E402
from smconfig.legacy import (CONTENT_EDITS, DIR_MOVE_EXCLUDE, DIR_MOVES, MOVES, SECRET_MOVES,  # noqa: E402
                             SHOW_SITE_KEYS)

SPECTRE = Path("/mnt/c/Users/Greg/OneDrive/dev/local-automation")
OLD_MARKERS = ("config/room/room.prod.yaml", "routes/data/publish-destinations.json", "routes/data/rooms.json",
               "deploy/sm-stream.env", "deploy/sm-show.env", "deploy/sm-dot.env", "config/room/patch.yaml",
               "config/room/dot.json", "config/room/fixtures", "config/room/rf/")


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.problems = 0

    def ok(self, msg: str) -> None:
        self._add("  ok   ", msg)

    def do(self, msg: str) -> None:
        self._add("  do   ", msg)

    def note(self, msg: str) -> None:
        self._add("  note ", msg)

    def bad(self, msg: str) -> None:
        self.problems += 1
        self._add("  FAIL ", msg)

    def head(self, msg: str) -> None:
        self._add("", f"\n== {msg}")

    def _add(self, tag: str, msg: str) -> None:
        line = f"{tag}{msg}"
        self.lines.append(line)
        print(line, flush=True)


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def expand_show_urls(doc, dep):
    """show.yaml as show_service reads it: its {room}/{flask}/{show} placeholders become this deployment's loopback
    URLs (show_service.config.expand_urls, given dep's URLs so it works before .env names the deployment).
    show_service.contract selects the deployment when it is imported, so on a machine whose .env doesn't name one yet
    (the plan, run before --apply writes SM_DEPLOYMENT) this run's --deployment is selected, in this process only."""
    os.environ.setdefault("SM_DEPLOYMENT", dep.id)
    from show_service.config import URL_SERVICES, expand_urls  # noqa: PLC0415
    return expand_urls(doc, {s: dep.url(s) for s in URL_SERVICES})


def git_blob(rev: str, rel: str) -> bytes | None:
    r = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=REPO, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def pre_layout_rev() -> str | None:
    """The commit just before deployments/ arrived: ORIG_HEAD if it predates the layout, else the parent of the
    commit that added deployments/finsbury/deployment.yaml."""
    orig = git("rev-parse", "--verify", "-q", "ORIG_HEAD", check=False).strip()
    if orig and git_blob(orig, "deployments/finsbury/deployment.yaml") is None:
        return orig
    added = git("log", "--diff-filter=A", "--format=%H", "--", "deployments/finsbury/deployment.yaml",
                check=False).split()
    return f"{added[-1]}^" if added else None


# --------------------------------------------------------------------------------------------------------------------

def step_env(dep_id: str, apply: bool, rep: Report) -> None:
    rep.head("1. .env selects the deployment")
    envf = smconfig.ENV_FILE
    current = smconfig._read_env_file(envf).get("SM_DEPLOYMENT", "")
    if current == dep_id:
        rep.ok(f".env already has SM_DEPLOYMENT={dep_id}")
        return
    if current:
        rep.bad(f".env has SM_DEPLOYMENT={current}, not {dep_id}: fix it by hand if that is wrong")
        return
    rep.do(f"add SM_DEPLOYMENT={dep_id} to {envf}")
    if apply:
        text = envf.read_text() if envf.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n# The deployment this machine runs (deployments/<id>/; deployments/README.md). Added by "
                 f"scripts/migrate_deployment.py on {_dt.date.today().isoformat()}.\nSM_DEPLOYMENT={dep_id}\n")
        envf.write_text(text)


def _leftover_pairs(dep: smconfig.Deployment) -> list[tuple[Path, Path]]:
    pairs = [(REPO / old, dep.path(*new.split("/"))) for old, new in MOVES.items()]
    for old_dir, new_dir in DIR_MOVES.items():
        od = REPO / old_dir
        if not od.is_dir():
            continue
        for p in sorted(od.rglob("*")):
            rel = p.relative_to(REPO).as_posix()
            if p.is_dir() or any(rel == x or rel.startswith(x + "/") for x in DIR_MOVE_EXCLUDE):
                continue
            pairs.append((p, dep.path(*new_dir.split("/"), *p.relative_to(od).parts)))
    return pairs


def step_leftovers(dep: smconfig.Deployment, apply: bool, rep: Report) -> None:
    rep.head("2. Files left in old locations")
    found = False
    for old, new in _leftover_pairs(dep):
        if not old.exists() or old.is_symlink():
            continue
        found = True
        rel = old.relative_to(REPO)
        if new.exists():
            if filecmp.cmp(old, new, shallow=False):
                rep.do(f"remove {rel}: an identical copy is at {new.relative_to(REPO)}")
                if apply:
                    old.unlink()
            else:
                rep.bad(f"{rel} differs from {new.relative_to(REPO)}: merge them by hand (diff -u {rel} "
                        f"{new.relative_to(REPO)}), then delete the old one and re-run")
        else:
            rep.do(f"move {rel} -> {new.relative_to(REPO)}")
            if apply:
                new.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(new))
    for old_dir in list(DIR_MOVES) + ["deploy"]:
        d = REPO / old_dir
        if apply and d.is_dir() and not any(p.is_file() for p in d.rglob("*")) and old_dir not in DIR_MOVE_EXCLUDE:
            if not any(str(x).startswith(old_dir) for x in DIR_MOVE_EXCLUDE):
                shutil.rmtree(d, ignore_errors=True)
    if not found:
        rep.ok("nothing left behind")


def step_secrets(apply: bool, rep: Report) -> None:
    rep.head("3. Secrets out of the repo, and this user's only")
    for old, new in SECRET_MOVES.items():
        dest = smconfig.secret(*new.split("/"))
        if dest.exists():
            rep.ok(f"{dest} exists")
            if (REPO / old).exists():
                if filecmp.cmp(REPO / old, dest, shallow=False):
                    rep.do(f"remove the repo-root copy {old}")
                    if apply:
                        (REPO / old).unlink()
                else:
                    rep.bad(f"{old} differs from {dest}: keep the right one by hand")
            continue
        data = (REPO / old).read_bytes() if (REPO / old).exists() else None
        src = old if data is not None else None
        if data is None:
            commits = git("log", "--all", "--format=%H", "-n", "1", "--diff-filter=D", "--", old, check=False).split()
            if commits:
                data = git_blob(f"{commits[0]}^", old)
                src = f"git {commits[0][:10]}^:{old}"
        if data is None:
            rep.note(f"{old}: not in the repo or its history; nothing to move (pair the TV again if it is needed)")
            continue
        rep.do(f"write {dest} (0600) from {src}")
        if apply:
            smconfig.SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(dest.parent, 0o700)
            dest.write_bytes(data)
            os.chmod(dest, 0o600)
            if (REPO / old).exists():
                (REPO / old).unlink()
    step_private(apply, rep)


def step_private(apply: bool, rep: Report) -> None:
    """.env, the kiosk's .env and ~/.config/screen-machine/ readable by this user only. A mode that lets group or
    other users in is narrowed (never widened); a file that isn't there is skipped."""
    for p, want in smsecrets.private_paths():
        if not p.exists():
            continue
        cur = stat.S_IMODE(p.stat().st_mode)
        if not cur & 0o077:
            rep.ok(f"{smsecrets.shown(p)} is {cur:04o}: this user's only")
            continue
        rep.do(f"chmod {want:o} {smsecrets.shown(p)} (it {'was' if apply else 'is'} {cur:04o}: other users on this "
               "machine can open it)")
        if apply:
            try:
                os.chmod(p, want)
            except OSError as e:
                rep.note(f"couldn't chmod {smsecrets.shown(p)}: {e}; do it by hand")


def _get(d, dotted):
    for k in dotted.split("."):
        d = d[k]
    return d


def _env_pairs(text: str, expand: bool, dep: smconfig.Deployment | None = None) -> dict[str, str]:
    import re as _re

    out = smconfig._parse_env_text(text)
    if expand:
        ports = (dep or smconfig.deployment()).ports

        def sub(m):
            name = m.group(1)
            if name.startswith("PORT_") and name[5:].lower() in ports:
                return str(ports[name[5:].lower()])
            return smconfig.env(name)

        out = {k: _re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, v) for k, v in out.items()}
    return out


def _apply_edits(rel: str, data):
    for dest_id, dotted, value in CONTENT_EDITS.get(rel, []):
        for d in data if isinstance(data, list) else []:
            if isinstance(d, dict) and d.get("id") == dest_id:
                cur = d
                *parents, last = dotted.split(".")
                for k in parents:
                    cur = cur.setdefault(k, {})
                cur[last] = value
    return data


def same_content(rel: str, before: bytes, p: Path, dep: smconfig.Deployment | None = None) -> str | None:
    """Why the new file holds the same data as the old content (None when it does not)."""
    after = p.read_bytes()
    if after == before:
        return "identical"
    import yaml

    try:
        if p.suffix == ".json":
            if _apply_edits(rel, json.loads(before)) == json.loads(after):
                return "same data" + (" with the declared edits" if rel in CONTENT_EDITS else "; comments differ")
        elif p.suffix in (".yaml", ".yml"):
            if yaml.safe_load(before) == yaml.safe_load(after):
                return "same data; comments differ"
        elif p.suffix == ".env":
            if _env_pairs(before.decode(), False) == _env_pairs(after.decode(), True, dep):
                return "same values once ${VAR}s expand; comments differ"
    except Exception:  # noqa: BLE001 - a parse failure is a difference
        return None
    return None


def step_prove(dep: smconfig.Deployment, rev: str | None, rep: Report) -> None:
    rep.head(f"4. Moved files match their old content at {rev or '?'}")
    if not rev:
        rep.bad("cannot find the pre-layout commit; pass --from-rev <commit before the pull>")
        return
    import yaml

    for old, new in MOVES.items():
        before = git_blob(rev, old)
        p = dep.path(*new.split("/"))
        if before is None:
            rep.note(f"{old}: absent at {rev}; nothing to compare")
            continue
        if not p.exists():
            rep.bad(f"{new}: missing (it was {old})")
        elif why := same_content(new, before, p, dep):
            rep.ok(f"{new} == {rev}:{old} ({why})")
        else:
            rep.bad(f"{new} differs from {rev}:{old} (git diff {rev}:{old} {p.relative_to(REPO)})")
    # Device files in old directories at rev.
    for old_dir, new_dir in DIR_MOVES.items():
        listing = git("ls-tree", "-r", "--name-only", rev, "--", old_dir, check=False).split()
        for rel in listing:
            if any(rel == x or rel.startswith(x + "/") for x in DIR_MOVE_EXCLUDE):
                continue
            p = dep.path(*new_dir.split("/"), *Path(rel).relative_to(old_dir).parts)
            before = git_blob(rev, rel)
            if not p.exists():
                rep.bad(f"{p.relative_to(REPO)}: missing (it was {rel})")
            elif why := same_content(str(p.relative_to(dep.dir)), before, p, dep):
                rep.ok(f"{p.relative_to(REPO)} == {rev}:{rel} ({why})")
            else:
                rep.bad(f"{p.relative_to(REPO)} differs from {rev}:{rel}")
    before = git_blob(rev, "config/room/show.yaml")
    if before is not None:
        old_show = yaml.safe_load(before) or {}
        layered = smconfig.load_layered_yaml(smconfig.shared("room", "show.yaml"), dep.path("show.yaml"))
        layered = expand_show_urls(layered, dep)         # the shared file's {room}/{flask}/{show} loopback URLs
        if layered == old_show:
            rep.ok(f"config/room/show.yaml + {dep.id}/show.yaml == {rev}:config/room/show.yaml (layered)")
        else:
            diffs = [k for k in sorted(set(old_show) | set(layered)) if old_show.get(k) != layered.get(k)]
            rep.bad(f"layered show settings differ from {rev}:config/room/show.yaml in: {', '.join(diffs)}")
        for key in SHOW_SITE_KEYS:
            try:
                _get(yaml.safe_load(smconfig.shared('room', 'show.yaml').read_text()), key)
                rep.bad(f"config/room/show.yaml still holds the site key {key}")
            except (KeyError, TypeError):
                pass


def step_check(dep: smconfig.Deployment, apply: bool, rep: Report) -> None:
    rep.head("5. smconfig check")
    problems, notes = smcheck.check(dep)
    for n in notes:
        rep.note(n)
    for p in problems:
        rep.bad(p)
    if not problems:
        rep.ok(f"deployment {dep.id} checks clean")
    if not apply:
        rep.note("the effective config is saved to logs/ by --apply")
        return
    logs = REPO / "logs"
    logs.mkdir(exist_ok=True)
    out = logs / f"smconfig-{dep.id}-{_dt.datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(smcheck.dump(dep), indent=1, sort_keys=True))
    rep.ok(f"effective config saved to {out.relative_to(REPO)}")


def step_outside(rep: Report) -> None:
    rep.head("6. Old paths named outside this repo")
    roots = [p for p in (SPECTRE / "src", SPECTRE / "scripts", Path.home() / "wsl-autostart.sh") if p.exists()]
    if not roots:
        rep.note(f"no Spectre checkout at {SPECTRE}; check its modules by hand for: {', '.join(OLD_MARKERS)}")
        return
    hits, comments = [], []
    for root in roots:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file() and p.suffix in
                                               (".ts", ".js", ".ps1", ".sh", ".json", ".md", ".py") and
                                               "node_modules" not in p.parts]
        for f in files:
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for m in OLD_MARKERS:
                    if m in line:
                        is_comment = line.lstrip().startswith(("//", "#", "*", "/*", "<#"))
                        (comments if is_comment else hits).append(f"{f}:{n}: {m}")
    for h in hits:
        rep.bad(f"still uses an old path: {h}")
    for c in comments:
        rep.note(f"a comment names an old path (update when convenient): {c}")
    if not hits:
        rep.ok("no code in Spectre or the boot script uses an old path")


def _venvs() -> list[str]:
    """Every ~/.venvs/sm-* with a python. Any of them may import smconfig: the services and their workers (dot, the
    piano and score workers), and the tools run with their own venv (tools/room/audio_cal with sm-roundtrip)."""
    root = Path.home() / ".venvs"
    return sorted(p.name for p in root.glob("sm-*") if (p / "bin" / "python").exists()) if root.is_dir() else []


def _pyyaml(name: str, apply: bool, rep: Report) -> None:
    """One report line for one venv: PyYAML is there, would be installed, was installed, or could not be (with the
    command to run by hand). A failed install is a problem in the report; the run carries on."""
    py = Path.home() / ".venvs" / name / "bin" / "python"
    by_hand = f"{py} -m pip install PyYAML"
    where = f"PyYAML in ~/.venvs/{name}"
    try:
        has = subprocess.run([str(py), "-c", "import yaml"], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        has = False
    if has:
        rep.ok(f"{where}: there")
        return
    if not apply:
        rep.do(f"{where}: missing; --apply installs it (by hand: {by_hand})")
        return
    try:
        r = subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "PyYAML"],
                           capture_output=True, text=True, timeout=600)
        ok, err = r.returncode == 0, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, err = False, str(e)
    if ok:
        rep.ok(f"{where}: installed now")
        return
    why = next((ln.strip() for ln in reversed(err.splitlines()) if ln.strip()), "no message")[:200]
    rep.bad(f"{where}: MISSING, and pip could not install it ({why}). Run by hand: {by_hand}")


def _append_env(key: str, value: str, why: str) -> None:
    envf = smconfig.ENV_FILE
    text = envf.read_text() if envf.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    envf.write_text(text + f"\n# {why} Added by scripts/migrate_deployment.py on {_dt.date.today().isoformat()}.\n"
                    f"{key}={value}\n")


def step_ngrok(dep: smconfig.Deployment, rev: str | None, apply: bool, rep: Report) -> None:
    """start.sh used to carry this machine's ngrok domain as its default; the public repo no longer does, so .env
    must. Recovered from the old start.sh when .env lacks it."""
    if not dep.enabled("ngrok") or smconfig.env("NGROK_DOMAIN"):
        if dep.enabled("ngrok"):
            rep.ok("NGROK_DOMAIN is set in .env")
        return
    import re as _re

    old = (git_blob(rev, "start.sh") or b"").decode(errors="replace") if rev else ""
    m = _re.search(r'NGROK_DOMAIN="\$\{NGROK_DOMAIN:-([^}"]+)\}"', old)
    if not m:
        rep.bad("capability ngrok is on but .env has no NGROK_DOMAIN: add NGROK_DOMAIN=<your ngrok domain> to .env")
        return
    rep.do(f"add NGROK_DOMAIN to .env (the old start.sh default, from {rev}:start.sh)")
    if apply:
        _append_env("NGROK_DOMAIN", m.group(1), "The ngrok tunnel's domain (start.sh's old built-in default).")


def step_machine(dep: smconfig.Deployment, apply: bool, rep: Report) -> None:
    rep.head("7. This machine: settings and Python environments")
    import re as _re

    needed = set()
    for f in sorted(dep.path("services").glob("*.env")) if dep.path("services").is_dir() else []:
        needed |= set(_re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", "\n".join(
            ln for ln in f.read_text().splitlines() if not ln.lstrip().startswith("#"))))
    needed = {v for v in needed if not (v.startswith("PORT_") and v[5:].lower() in dep.ports)}
    # A key written twice in .env: the last one wins (as python-dotenv and smconfig read it); room_service's old
    # reader took the first, so a duplicate could change the hosts room_service allows. Make each key appear once.
    if smconfig.ENV_FILE.exists():
        keys = [ln.split("=", 1)[0].strip().removeprefix("export ").strip()
                for ln in smconfig.ENV_FILE.read_text().splitlines() if "=" in ln and not ln.lstrip().startswith("#")]
        dups = sorted({k for k in keys if keys.count(k) > 1})
        if dups:
            rep.bad(f".env sets {', '.join(dups)} more than once: keep one line each (the last one wins now)")
        else:
            rep.ok(".env sets each key once")
    for var in sorted(needed):
        if smconfig.env(var):
            rep.ok(f"{var} is set ({smconfig.env(var)}); the service flags use it")
        else:
            rep.bad(f"{var} is not set in .env, and deployments/{dep.id}/services/*.env use it")
    # Every process imports smconfig, which reads the manifest with PyYAML: one line per venv.
    venvs = _venvs()
    if not venvs:
        rep.note(f"no ~/.venvs/sm-* here ({Path.home() / '.venvs'}); nothing to check for PyYAML")
    for name in venvs:
        _pyyaml(name, apply, rep)


def _get_json(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - loopback only
        return json.loads(r.read().decode())


def step_live(dep: smconfig.Deployment, rep: Report) -> None:
    rep.head("Live services answer as the deployment says")
    caps = dep.capabilities
    try:
        _get_json(f"{dep.url('flask')}/api/health")
        rep.ok("Flask /api/health")
    except Exception as e:  # noqa: BLE001
        rep.bad(f"Flask /api/health: {e}")
        return
    try:
        info = _get_json(f"{dep.url('flask')}/api/deployment")
        if info.get("id") == dep.id and info.get("capabilities") == caps:
            rep.ok(f"/api/deployment says {dep.id} with the manifest's capabilities")
        else:
            rep.bad(f"/api/deployment says {info}")
    except Exception as e:  # noqa: BLE001
        rep.bad(f"/api/deployment: {e}")
    want = {d["id"] for d in smconfig.destinations(dep) if not d.get("hidden")}
    try:
        got = {d["id"] for d in _get_json(f"{dep.url('flask')}/api/publish-destinations")}
        (rep.ok if want <= got | {d["id"] for d in smconfig.destinations(dep) if d.get("hidden")} else rep.bad)(
            f"/api/publish-destinations lists {len(got)} destinations")
    except Exception as e:  # noqa: BLE001
        rep.bad(f"/api/publish-destinations: {e}")
    if caps["room"]:
        try:
            st = _get_json(f"{dep.url('room')}/api/v1/status")
            screens = set((st.get("screens") or {}).keys()) if isinstance(st.get("screens"), dict) else \
                {s.get("id") for s in st.get("screens") or []}
            live = set(smconfig.live_screens(dep))
            (rep.ok if live <= screens else rep.bad)(f"room_service screens {sorted(screens)} (live: {sorted(live)})")
            rooms = {r.get("id") for r in st.get("rooms") or []}
            (rep.ok if set(smconfig.rooms(dep)) == rooms else rep.bad)(f"room_service rooms {sorted(rooms)}")
        except Exception as e:  # noqa: BLE001
            rep.bad(f"room_service /api/v1/status: {e}")
    for svc, cap in (("show", "show"), ("dot", "dot")):
        if not caps[cap]:
            continue
        try:
            _get_json(f"{dep.url(svc)}/healthz")
            rep.ok(f"{svc}_service /healthz")
        except Exception as e:  # noqa: BLE001
            rep.bad(f"{svc}_service /healthz: {e}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployment", required=True, help="the deployment this machine runs, e.g. finsbury")
    ap.add_argument("--apply", action="store_true", help="make the changes (default: only report the plan)")
    ap.add_argument("--from-rev", help="the commit this machine ran before the pull (default: ORIG_HEAD)")
    ap.add_argument("--verify-live", action="store_true", help="only check the running services")
    a = ap.parse_args(argv)
    try:
        dep = smconfig.load(a.deployment)
    except smconfig.DeploymentError as e:
        print(f"migrate: {e}", file=sys.stderr)
        return 2
    rep = Report()
    print(f"deployment {dep.id}: {dep.dir}  ({'APPLY' if a.apply else 'plan only; add --apply to make changes'})")
    if a.verify_live:
        step_live(dep, rep)
    else:
        step_env(dep.id, a.apply, rep)
        step_leftovers(dep, a.apply, rep)
        step_secrets(a.apply, rep)
        step_prove(dep, a.from_rev or pre_layout_rev(), rep)
        step_check(dep, a.apply, rep)
        step_outside(rep)
        step_machine(dep, a.apply, rep)
        step_ngrok(dep, a.from_rev or pre_layout_rev(), a.apply, rep)
    print(f"\n{'OK' if not rep.problems else f'{rep.problems} problem(s)'}")
    if a.apply and not a.verify_live:
        logs = REPO / "logs"
        logs.mkdir(exist_ok=True)
        log = logs / f"migration-{dep.id}-{_dt.datetime.now().strftime('%Y%m%dT%H%M%S')}.txt"
        log.write_text("\n".join(rep.lines) + "\n")
        print(f"this run's report (what it moved and wrote): {log.relative_to(REPO)}")
    return 1 if rep.problems else 0


if __name__ == "__main__":
    sys.exit(main())
