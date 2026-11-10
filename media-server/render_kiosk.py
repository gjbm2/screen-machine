#!/usr/bin/env python3
"""Render the kiosk's remote scripts for this deployment (the kiosk block of deployments/<id>/deployment.yaml).

The files in media-server/remote-scripts/ that name the site (the URLs the kiosk Chromes load, the deadman's probe URL,
the light sensor's server address, the kiosk login in the units and sensor scripts) carry tokens instead of the values;
the deploy scripts install the rendered copies:

    @@KIOSK_BASE_URL@@        kiosk.base_url                  e.g. http://95.141.21.170:8000
    @@KIOSK_BASE_HOST@@       the host part of kiosk.base_url e.g. 95.141.21.170
    @@KIOSK_USER@@            kiosk.user (the kiosk's login)  e.g. gjbm2
    @@KIOSK_SCREEN_<NAME>@@   the screen id whose kiosk.screens entry has name <name> (upper case),
                              e.g. @@KIOSK_SCREEN_NORTH@@ -> north-screen

Usage (from the repo; SM_DEPLOYMENT selects the deployment, --deployment overrides it):
    render_kiosk.py list                     the files that carry tokens
    render_kiosk.py render FILE              one file (relative to remote-scripts/), rendered, to stdout
    render_kiosk.py render-dir OUT           the whole remote-scripts/ tree into OUT, tokens filled (modes kept)
    render_kiosk.py check [--ref REF]        render every templated file and compare it with `git show REF:<file>`;
                                             exit 1 on any difference. The owner's rule: the kiosk/TV page URLs never
                                             change, so for finsbury the rendered files must equal the files at
                                             BASELINE_REF (the last commit before they became templates).
"""
from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REMOTE_DIR = HERE / "remote-scripts"
BASELINE_REF = "68a5022d"

sys.path.insert(0, str(REPO))
import smconfig  # noqa: E402

TOKEN_RE = re.compile(r"@@(KIOSK_[A-Z0-9_]+)@@")


class RenderError(RuntimeError):
    pass


def values(dep_id: str | None = None) -> dict[str, str]:
    """Token -> value from the deployment's kiosk block."""
    dep = smconfig.load(dep_id)
    k = dep.kiosk
    if not k:
        raise RenderError(f"deployment {dep.id!r} has no kiosk block in {dep.path(smconfig.MANIFEST)}")
    out: dict[str, str] = {}
    if k.get("base_url"):
        base = str(k["base_url"]).rstrip("/")
        out["KIOSK_BASE_URL"] = base
        out["KIOSK_BASE_HOST"] = urlsplit(base).hostname or ""
    if k.get("user"):
        out["KIOSK_USER"] = str(k["user"])
    for screen_id, cfg in (k.get("screens") or {}).items():
        name = str((cfg or {}).get("name") or "")
        if name:
            out[f"KIOSK_SCREEN_{re.sub(r'[^A-Z0-9]', '_', name.upper())}"] = str(screen_id)
    return out


def templated_files() -> list[str]:
    """remote-scripts files (relative paths) that carry tokens."""
    found = []
    for p in sorted(REMOTE_DIR.rglob("*")):
        if p.is_file():
            try:
                if TOKEN_RE.search(p.read_bytes().decode("utf-8")):
                    found.append(p.relative_to(REMOTE_DIR).as_posix())
            except UnicodeDecodeError:
                continue
    return found


def render_text(text: str, vals: dict[str, str], name: str = "") -> str:
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        v = vals.get(m.group(1))
        if not v:
            missing.append(m.group(1))
            return m.group(0)
        return v

    out = TOKEN_RE.sub(sub, text)
    if missing:
        raise RenderError(f"{name or 'template'}: no value for {', '.join(dict.fromkeys(missing))} "
                          "(the deployment's kiosk block: base_url, user, screens.<id>.name)")
    return out


def render_file(rel: str, vals: dict[str, str]) -> str:
    return render_text((REMOTE_DIR / rel).read_bytes().decode("utf-8"), vals, rel)   # bytes: no newline translation


def render_dir(out: Path, vals: dict[str, str]) -> list[str]:
    """Copy remote-scripts/ into ``out`` with every templated file rendered. Returns the rendered files."""
    templ = set(templated_files())
    out.mkdir(parents=True, exist_ok=True)
    for p in sorted(REMOTE_DIR.rglob("*")):
        rel = p.relative_to(REMOTE_DIR).as_posix()
        dst = out / rel
        if p.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel in templ:
            dst.write_bytes(render_file(rel, vals).encode("utf-8"))
            shutil.copymode(p, dst)
        else:
            shutil.copy2(p, dst)
    return sorted(templ)


def check(ref: str, vals: dict[str, str]) -> list[str]:
    """Differences between each rendered template and the same file at ``ref`` (empty: identical)."""
    problems = []
    for rel in templated_files():
        repo_rel = (REMOTE_DIR / rel).relative_to(REPO).as_posix()
        try:
            want = subprocess.run(["git", "-C", str(REPO), "show", f"{ref}:{repo_rel}"], check=True,
                                  capture_output=True).stdout.decode("utf-8")
        except (OSError, subprocess.CalledProcessError) as e:
            problems.append(f"{repo_rel}: cannot read it at {ref}: {getattr(e, 'stderr', b'').decode().strip() or e}")
            continue
        got = render_file(rel, vals)
        if got != want:
            diff = "".join(difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                                f"{ref}:{repo_rel}", f"rendered:{repo_rel}"))
            problems.append(f"{repo_rel}: rendered file differs from {ref}\n{diff}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployment", help="a deployment id (default: SM_DEPLOYMENT)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("render")
    r.add_argument("file")
    d = sub.add_parser("render-dir")
    d.add_argument("out")
    c = sub.add_parser("check")
    c.add_argument("--ref", default=BASELINE_REF)
    a = ap.parse_args(argv)

    if a.cmd == "list":
        print("\n".join(templated_files()))
        return 0
    try:
        vals = values(a.deployment)
        if a.cmd == "render":
            sys.stdout.write(render_file(a.file, vals))
        elif a.cmd == "render-dir":
            for rel in render_dir(Path(a.out), vals):
                print(f"rendered {rel}")
        elif a.cmd == "check":
            problems = check(a.ref, vals)
            for p in problems:
                print(p)
            n = len(templated_files())
            print(f"{n - len(problems)}/{n} rendered kiosk files identical to {a.ref}" if problems
                  else f"ok: {n} rendered kiosk files identical to {a.ref}")
            return 1 if problems else 0
    except (smconfig.DeploymentError, RenderError, OSError) as e:
        print(f"render_kiosk: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
