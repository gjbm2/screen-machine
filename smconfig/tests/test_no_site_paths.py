"""Guard: no code builds a path to a deployment file by hand, or names an old (pre-deployments/) site path.

Code may build a path to a deployment file's name ("patch.yaml", "rooms.json", ...) only through the deployment:
smconfig's path()/secret()/state()/shared(), a Deployment's path()/host_path(), or Flask's findfile()/_load_json_once()
(which search the deployment directory first). Building one with `/`, Path(), os.path.join() or open() is the hand-
built path this test exists to stop. Any string naming an old (pre-deployments/) path is flagged wherever it is.
Docstrings and comments may say anything.
Tests, tools' historical run data, experiments and the migration machinery are exempt.
"""
from __future__ import annotations

import ast
from pathlib import Path

import smconfig
from smconfig.legacy import MOVES

REPO = smconfig.REPO_ROOT
SCAN_DIRS = ["room_service", "show_service", "stream_service", "dot_service", "piano_render", "routes", "lightmask",
             "utils", "tools", "scripts"]
SCAN_FILES = ["app.py", "config.py", "room_in_app.py", "overlay_ws_server.py", "connection_registry.py"]
EXEMPT_PARTS = {"tests", "__pycache__", "runs", "node_modules"}
EXEMPT_FILES = {"scripts/migrate_deployment.py"}

SITE_NAMES = {Path(new).name for new in MOVES.values()} | {"room.prod.yaml", "blinds.json", "glitterball.json",
                                                          "pinspots.json"}
SITE_NAMES -= {"sm-stream.env", "sm-show.env", "sm-dot.env"}   # shell-side; ctl resolves them through smconfig
OLD_PATHS = tuple(MOVES) + ("config/room/rf/blinds.json", "config/room/rf/glitterball.json",
                            "config/room/rf/pinspots.json", "src/data/publish-destinations.json", "deploy/sm-")
BUILDERS = {"Path", "PurePath", "PosixPath", "join", "open"}
ALLOWED_CALLS = {"path", "secret", "state", "shared", "host_path", "findfile", "_load_json_once", "joinpath"}


def _files():
    for f in SCAN_FILES:
        if (REPO / f).is_file():
            yield REPO / f
    for d in SCAN_DIRS:
        for p in (REPO / d).rglob("*.py"):
            rel = p.relative_to(REPO)
            if EXEMPT_PARTS & set(rel.parts) or rel.as_posix() in EXEMPT_FILES or p.name.startswith("test_"):
                continue
            yield p


def _docstring_nodes(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


def test_no_hand_built_site_paths():
    problems = []
    for p in _files():
        try:
            tree = ast.parse(p.read_text(), filename=str(p))
        except SyntaxError:
            continue
        docs = _docstring_nodes(tree)
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs:
                continue
            v = node.value
            if len(v) > 300:          # prose (help texts, headers written into files)
                continue
            old = next((o for o in OLD_PATHS if o in v), None)
            if old:
                problems.append(f"{p.relative_to(REPO)}:{node.lineno}: names the old path {old!r}")
                continue
            if v in SITE_NAMES or v == "deployments":
                # Flag the name only where it builds a path by hand: an operand of `/`, or an argument of Path(),
                # os.path.join() or open(), with no smconfig/findfile call in between. A name used as a label, in
                # a message, or joined onto a directory smconfig gave is fine.
                cur, verdict = node, None
                while id(cur) in parents and verdict is None:
                    cur = parents[id(cur)]
                    if isinstance(cur, ast.Call) and _call_name(cur) in ALLOWED_CALLS:
                        verdict = "ok"
                    elif isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                        verdict = "built"
                    elif isinstance(cur, ast.Call) and _call_name(cur) in BUILDERS:
                        verdict = "built"
                    elif isinstance(cur, (ast.stmt,)):
                        verdict = "ok"
                if verdict == "built":
                    problems.append(f"{p.relative_to(REPO)}:{node.lineno}: builds a path to {v!r} by hand")
    assert not problems, "site paths built by hand:\n" + "\n".join(problems)
