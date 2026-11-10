"""What each capability needs that must never be in git, and the tool that checks it and moves it between machines.

    python -m smconfig.secrets check                 # this deployment: every secret its capabilities use, set or missing
    python -m smconfig.secrets export <file>         # write this machine's SHAREABLE keys to <file> (0600)
    python -m smconfig.secrets import <file>         # add the keys in <file> that this machine's .env lacks

Values are never printed. There are two kinds of secret (deployments/README.md, "Secrets"):

* **Keys in .env** (KEY=value lines in the repo's untracked .env; every process reads them from there or from its
  environment). The API keys (LLM, RunPod, AssemblyAI, the Gmail alert sender) work on any machine: those are
  SHAREABLE, and export/import carries them through a file the owner keeps somewhere private (not the repo).
* **Files under ~/.config/screen-machine/** (smconfig.secret()): pairings and tokens made on one machine for its own
  devices (Hue bridge, Samsung TVs, the Dot. key, the Bloc hub key, Cloudflare). They stay with that machine; check
  only says whether they exist.

check also warns (paths only) when .env, the kiosk's media-server/local-scripts/.env, ~/.config/screen-machine/ or a
file in it can be opened by other users on this machine. Each should be this user's only: files 0600, the folder 0700.
scripts/migrate_deployment.py --apply sets those modes.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import smconfig
from smconfig import REPO_ROOT, DeploymentError, _parse_env_text, deployment, secret


@dataclass(frozen=True)
class Need:
    kind: str            # "env" (a .env key) or "file" (under ~/.config/screen-machine/)
    name: str            # the key, or the path relative to ~/.config/screen-machine/
    what: str            # what uses it, in a few words
    shareable: bool = False
    optional: bool = False   # the feature runs without it (less well)
    any_of: str = ""     # a group: one key of the group set is enough
    env_file: str = ""   # kind "env" only: the repo-relative file its consumer reads instead of .env (never shareable)


# capability -> what it needs. A capability not listed needs no secret.
NEEDS: dict[str, tuple[Need, ...]] = {
    "generation": (
        Need("env", "RUNPOD_API_KEY", "image/video generation on RunPod", shareable=True),
        Need("env", "RUNPOD_ID", "the RunPod endpoint", shareable=True),
        Need("env", "OPENAI_API_KEY", "prompt refining (config/llm.yaml)", shareable=True, any_of="llm"),
        Need("env", "GEMINI_API_KEY", "prompt refining (config/llm.yaml)", shareable=True, any_of="llm"),
    ),
    "art_tags": (
        Need("env", "OPENAI_API_KEY", "art tagging (config/llm.yaml)", shareable=True, any_of="llm"),
        Need("env", "GEMINI_API_KEY", "art tagging (config/llm.yaml)", shareable=True, any_of="llm"),
    ),
    "show": (
        Need("env", "OPENAI_API_KEY", "the listener's Describe it (config/llm.yaml)", shareable=True, optional=True,
             any_of="llm"),
        Need("env", "GEMINI_API_KEY", "the listener's Describe it (config/llm.yaml)", shareable=True, optional=True,
             any_of="llm"),
    ),
    "alexa": (
        Need("env", "ASSEMBLY_AI_KEY", "speech to text for voice requests", shareable=True, optional=True),
    ),
    "ngrok": (
        Need("env", "NGROK_DOMAIN", "the tunnel's domain (start.sh skips ngrok without it)"),
    ),
    "alerts": (
        Need("env", "GOOGLE_CLIENT_ID", "the Gmail alert sender", shareable=True),
        Need("env", "GOOGLE_CLIENT_SECRET", "the Gmail alert sender", shareable=True),
        Need("env", "GOOGLE_REFRESH_TOKEN", "the Gmail alert sender", shareable=True),
        Need("env", "ALERT_EMAIL_TO", "who alert e-mails go to", shareable=True),
    ),
    "kiosk_admin": (
        Need("env", "ADMIN_API_TOKEN", "the admin API's bearer token"),
        Need("env", "MEDIA_SERVER_PASSWORD", "the kiosk login's password, for sudo (routes/admin_api.py)",
             env_file="media-server/local-scripts/.env"),
    ),
    "room": (
        Need("file", "room_sensors.json", "light-sensor keys (read, but they gate nothing now)", optional=True),
    ),
    "blinds": (
        Need("file", "rf/bloc_hub.json", "the Bloc hub key: moving the blinds needs it (ignored unless 0600)"),
    ),
    "tv_power": (
        Need("file", "samsung", "Samsung Frame pairing tokens (made by pairing each TV with this machine)"),
    ),
    "hue": (
        Need("file", "hue", "the Hue bridge pairing (python -m room_service.hue.pair)"),
    ),
    "dot": (
        Need("file", "dot.json", "the Dot. cloud API key"),
    ),
}


def needs_for(caps: dict[str, bool]) -> list[tuple[str, Need]]:
    return [(cap, n) for cap, ns in NEEDS.items() if caps.get(cap) for n in ns]


def shareable_keys() -> list[str]:
    seen: list[str] = []
    for ns in NEEDS.values():
        for n in ns:
            if n.kind == "env" and n.shareable and not n.env_file and n.name not in seen:
                seen.append(n.name)
    return seen


def _env_now() -> dict[str, str]:
    try:
        file_vals = _parse_env_text(smconfig.ENV_FILE.read_text())
    except OSError:
        file_vals = {}
    out = {k: v for k, v in file_vals.items() if v}
    out.update({k: v for k, v in os.environ.items() if v.strip()})
    return out


def _is_set(n: Need, env: dict[str, str]) -> bool:
    if n.kind == "env" and n.env_file:
        try:
            return bool(_parse_env_text((REPO_ROOT / n.env_file).read_text()).get(n.name, "").strip())
        except OSError:
            return False
    if n.kind == "env":
        return bool(env.get(n.name, "").strip())
    p = secret(*n.name.split("/"))
    return p.is_file() and p.stat().st_size > 0 or p.is_dir() and any(p.iterdir())


def check() -> int:
    """Print every secret the deployment's capabilities use, set or missing. Exit 1 when a needed one is missing."""
    dep = deployment()
    env = _env_now()
    rows = needs_for(dep.capabilities)
    print(f"deployment {dep.id}: secrets its capabilities use (values never shown)")
    if not rows:
        print("  none: no capability that needs a secret is on")
        _print_permission_warnings()
        return 0
    groups_ok = {n.any_of for _, n in rows if n.any_of and _is_set(n, env)}
    missing = 0
    done: set[tuple[str, str]] = set()
    for cap, n in rows:
        if (n.kind, n.name) in done:
            continue
        done.add((n.kind, n.name))
        ok = _is_set(n, env)
        where = (n.env_file or ".env") if n.kind == "env" else f"~/.config/screen-machine/{n.name}"
        if ok:
            state = "set"
        elif n.any_of and n.any_of in groups_ok:
            state = "not set (another key of its group is)"
        elif n.optional:
            state = "missing (optional)"
        else:
            state = "MISSING"
            missing += 1
        share = "shareable" if n.shareable else "this machine's own"
        print(f"  {state:<38} {n.name:<22} {where:<42} {cap}: {n.what} [{share}]")
    if missing:
        print(f"{missing} missing. Shareable keys: `python -m smconfig.secrets import <file>` from the owner's private "
              "copy; a machine's own: pair or create it on this machine (deployments/README.md, \"Secrets\").")
    _print_permission_warnings()
    return 1 if missing else 0


# --- who else can read them ------------------------------------------------------------------------------------------

def private_paths() -> list[tuple[Path, int]]:
    """Where this machine's secrets live, with the mode each should have (this user's only): the repo's .env, any env
    file a need names (the kiosk's media-server/local-scripts/.env), and ~/.config/screen-machine/."""
    out = [(smconfig.ENV_FILE, 0o600)]
    for f in sorted({n.env_file for ns in NEEDS.values() for n in ns if n.env_file}):
        out.append((REPO_ROOT / f, 0o600))
    out.append((smconfig.SECRETS_DIR, 0o700))
    return out


def open_mode(p: Path) -> int | None:
    """p's mode when group or other users have any access to it; None when it is this user's only (or absent)."""
    try:
        mode = stat.S_IMODE(p.stat().st_mode)
    except OSError:
        return None
    return mode if mode & 0o077 else None


def shown(p: Path) -> str:
    """A path for a message: repo-relative inside the repo, ~/... under the home folder."""
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + p.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(p)


def permission_warnings() -> list[str]:
    """One plain line per secret file or folder that other users on this machine can open. Paths only, never values."""
    out = []
    for p, want in private_paths():
        mode = open_mode(p)
        if mode is not None:
            out.append(f"{shown(p)} is {mode:04o}: other users on this machine can open it (chmod {want:o} {shown(p)})")
    d = smconfig.SECRETS_DIR
    for p in sorted(d.rglob("*")) if d.is_dir() else []:
        mode = open_mode(p) if p.is_file() else None
        if mode is not None:
            out.append(f"{shown(p)} is {mode:04o}: other users on this machine can read it (chmod 600 {shown(p)})")
    return out


def _print_permission_warnings() -> None:
    warns = permission_warnings()
    for w in warns:
        print(f"warning: {w}")
    if warns:
        print("Secrets should be this user's only. scripts/migrate_deployment.py --apply fixes .env, the kiosk's .env "
              "and the folder; chmod the others as shown.")


def _outside_repo(p: Path) -> bool:
    try:
        p.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return True
    return False


_WINDOWS_DRIVE = re.compile(r"^/mnt/[a-zA-Z](/|$)")
_NO_UNIX_MODES = {"drvfs", "9p", "v9fs", "vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk", "cifs", "smb3"}


def _mount_fstype(p: Path) -> str:
    """The file system type of the mount that holds p (from /proc/mounts; "" when unknown)."""
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return ""
    best, fstype = "", ""
    target = str(p.resolve())
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt = parts[1].replace("\\040", " ")
        if (target == mnt or target.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
            best, fstype = mnt, parts[2]
    return fstype


def _unprotected(p: Path) -> bool:
    """True when 0600 doesn't protect p: a Windows drive (WSL's /mnt/<letter>/, drvfs or 9p, often synced to the
    cloud by OneDrive), or a file system that kept group or other bits after chmod."""
    return (bool(_WINDOWS_DRIVE.match(str(p.resolve()))) or _mount_fstype(p) in _NO_UNIX_MODES
            or open_mode(p) is not None)


def export(dest: Path) -> int:
    """Write this machine's shareable keys (the ones it has) to dest, 0600. Refuses a path inside the repo. On a
    Windows drive, or wherever 0600 doesn't hold, it still writes, but warns and says to encrypt instead."""
    if not _outside_repo(dest):
        print(f"refused: {dest} is inside the repo (which is public). Put it somewhere private.", file=sys.stderr)
        return 2
    env = _env_now()
    keys = [k for k in shareable_keys() if env.get(k)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# screen-machine shareable keys (python -m smconfig.secrets export). Keep private; never commit.\n")
        for k in keys:
            f.write(f"{k}={_quote(env[k])}\n")
    try:
        os.chmod(dest, 0o600)
        unprotected = _unprotected(dest)
    except OSError:
        unprotected = True
    print(f"wrote {len(keys)} key(s) to {dest}{'' if unprotected else ' (0600)'}: {', '.join(keys) or 'none'}")
    if unprotected:
        print(f"warning: {dest} is on a Windows drive or a file system that ignores Unix permissions. 0600 isn't "
              "enforced there, and a folder that syncs (OneDrive) sends the keys to the cloud as plain text. If it "
              "syncs, delete it now. Export to this machine's own disk instead (~/screen-machine-keys.env), then "
              "encrypt the copy that travels with gpg -c (deployments/README.md, \"Secrets\").")
    skipped = [k for k in shareable_keys() if k not in keys]
    if skipped:
        print(f"not set here, so not written: {', '.join(skipped)}")
    return 0


def import_(src: Path, overwrite: bool = False) -> int:
    """Add src's keys to the repo's .env: only keys .env lacks (or all, with overwrite). .env ends 0600."""
    try:
        incoming = {k: v for k, v in _parse_env_text(src.read_text()).items() if v}
    except OSError as e:
        print(f"can't read {src}: {e}", file=sys.stderr)
        return 2
    try:
        lines = smconfig.ENV_FILE.read_text().splitlines()
    except OSError:
        lines = []
    have = _parse_env_text("\n".join(lines))
    added, replaced, kept = [], [], []
    for k, v in incoming.items():
        if have.get(k):
            if not overwrite or have[k] == v:
                kept.append(k)
                continue
            lines = [ln for ln in lines if not _defines(ln, k)]
            replaced.append(k)
        else:
            lines = [ln for ln in lines if not _defines(ln, k)]      # an empty KEY= line
            added.append(k)
        lines.append(f"{k}={_quote(v)}")
    if added or replaced:
        fd = os.open(smconfig.ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(smconfig.ENV_FILE, 0o600)
    print(f"added: {', '.join(added) or 'none'}")
    if replaced:
        print(f"replaced: {', '.join(replaced)}")
    if kept:
        print(f"kept this machine's own value: {', '.join(kept)}" + ("" if overwrite else " (--overwrite replaces)"))
    print("Restart the services that use them (start.sh for Flask, scripts/ctl restart show).")
    return 0


def _quote(v: str) -> str:
    """A value as a .env line keeps it (_parse_env_text reads it back): quoted when it has a space, '#' or a quote."""
    if not any(c in v for c in " #'\"\t"):
        return v
    if "'" not in v:
        return f"'{v}'"
    if '"' not in v:
        return f'"{v}"'
    raise ValueError("a value with both kinds of quote can't be written to .env; set it by hand")


def _defines(line: str, key: str) -> bool:
    s = line.strip()
    if s.startswith("export "):
        s = s[7:].lstrip()
    return s.startswith(key + "=")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m smconfig.secrets", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="every secret this deployment's capabilities use: set or missing")
    e = sub.add_parser("export", help="write this machine's shareable keys to a private file (0600; it warns where "
                                      "that isn't enforced)")
    e.add_argument("file", type=Path)
    i = sub.add_parser("import", help="add a private file's keys to this machine's .env")
    i.add_argument("file", type=Path)
    i.add_argument("--overwrite", action="store_true", help="replace keys .env already has")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            return check()
        if a.cmd == "export":
            return export(a.file.expanduser())
        return import_(a.file.expanduser(), a.overwrite)
    except DeploymentError as err:
        print(err, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
