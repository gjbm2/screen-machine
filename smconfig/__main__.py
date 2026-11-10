"""python -m smconfig <command>

  which [REL]        the selected deployment's id and directory, or the absolute path of REL inside it
  get KEY            one value, for shell scripts: id, name, dir, capabilities.<cap>, ports.<svc>, urls.<svc>,
                     default_room, kiosk.<key>, host.<key>, path.<rel>, env.<NAME> (a .env machine setting;
                     empty when unset) (booleans print true/false)
  enabled CAP        exit 0 if the capability is on, 1 if off (for `if python -m smconfig enabled dot; then`)
  check              validate the deployment (manifest, required files, parsing, cross-references); exit 1 on problems
  dump               the effective configuration as JSON (what every process resolves); diff it across a change
  list               the deployments in this checkout

The deployment comes from SM_DEPLOYMENT (environment, else the repo's .env); `--deployment ID` overrides it.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import DeploymentError, available, load


def _get(dep, key: str):
    from .check import dump

    if key.startswith("path."):
        return str(dep.path(*key[len("path."):].split("/")))
    if key.startswith("env."):
        from . import env
        return env(key[len("env."):])
    d = dump(dep)
    cur = d
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(key)
    return cur


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m smconfig", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployment", help="a deployment id (default: SM_DEPLOYMENT)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("which")
    w.add_argument("rel", nargs="?")
    g = sub.add_parser("get")
    g.add_argument("key")
    e = sub.add_parser("enabled")
    e.add_argument("capability")
    sub.add_parser("check")
    sub.add_parser("dump")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        print("\n".join(available()))
        return 0
    try:
        dep = load(a.deployment)
    except DeploymentError as e:
        print(f"smconfig: {e}", file=sys.stderr)
        return 2

    if a.cmd == "which":
        print(dep.path(*a.rel.split("/")) if a.rel else f"{dep.id} {dep.dir}")
        return 0
    if a.cmd == "get":
        try:
            v = _get(dep, a.key)
        except KeyError:
            print(f"smconfig: no value {a.key!r}", file=sys.stderr)
            return 1
        if isinstance(v, bool):
            print("true" if v else "false")
        elif isinstance(v, (dict, list)):
            print(json.dumps(v))
        elif v is None:
            print("")
        else:
            print(v)
        return 0
    if a.cmd == "enabled":
        try:
            return 0 if dep.enabled(a.capability) else 1
        except KeyError as e:
            print(f"smconfig: {e}", file=sys.stderr)
            return 2
    if a.cmd == "dump":
        from .check import dump

        print(json.dumps(dump(dep), indent=1, sort_keys=True))
        return 0
    if a.cmd == "check":
        from .check import check

        problems, notes = check(dep)
        print(f"deployment {dep.id} ({dep.name}): {dep.dir}")
        for n in notes:
            print(f"  note: {n}")
        for p in problems:
            print(f"  PROBLEM: {p}")
        print("  ok" if not problems else f"  {len(problems)} problem(s)")
        return 1 if problems else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
