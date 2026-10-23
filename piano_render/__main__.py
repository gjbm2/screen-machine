"""python -m piano_render [--config PATH] [--cache DIR] [--no-idle] COMMAND ...

  list [--json]                                        instruments (available or why not) and rooms
  render MID [--instrument ID] [--room R] [--no-realism] [--out FILE] [--force] [--json]
                                                       render offline to FILE (default "<piece> - <instrument>.flac"
                                                       here), or with --cache / $PIANO_RENDER_CACHE into that cache
                                                       (or found there); prints the FLAC path
  fetch [IDS...] [--status] [--registry PATH]         install instruments/rooms/sfizz (piano_render.fetch)
  play-pc22 FILE_OR_MID [--instrument ID] [--room R] [--no-realism]
                                                       play on PC22's speakers (piano_render.pc22; a .mid is
                                                       rendered as it plays, nothing kept)
  audition [--out DIR] [--instruments ID ...] [--excerpts N ...] [--no-ab] [--work DIR] [--json]
                                                       the audition set: excerpts x instruments as tagged 16-bit
                                                       FLACs (piano_render.audition; default out: PC22's
                                                       C:\\Users\\Greg\\Music\\Piano audition)

--config (or $PIANO_RENDER_CONFIG) picks the instruments.json; --cache (or $PIANO_RENDER_CACHE) names a render cache
(there is none unless named: the live test piano renders as it plays and keeps nothing). render and audition drop to
idle CPU priority first (SCHED_IDLE, nice 19) unless --no-idle: this box also runs the live TV streams. fetch, play-pc22 and audition live in modules of their own, imported only when used: if one
is missing, the other commands still work.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from . import instruments as _instruments

REPO = Path(__file__).resolve().parent.parent
TEST_PIANO_DIR = REPO / "config" / "live" / "test-piano"


def lower_priority() -> None:
    try:
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
    except (AttributeError, OSError):
        pass
    try:
        os.nice(19 - os.nice(0))
    except OSError:
        pass


def optional_module(name: str, what: str):
    """Import piano_render.<name>, or print why it can't be used and return None."""
    full = f"piano_render.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name in (full, "piano_render." + name):
            print(f"piano_render: '{what}' needs {full} ({name}.py), which is not installed yet.", file=sys.stderr)
        else:
            print(f"piano_render: '{what}' cannot load {full}: missing dependency {e.name!r} ({e}).", file=sys.stderr)
        return None
    except ImportError as e:
        print(f"piano_render: '{what}' cannot load {full}: {e}", file=sys.stderr)
        return None


class _TTYProgress:
    def __init__(self, label: str):
        self.label = label
        self.on = sys.stderr.isatty()
        self.last = 0.0

    def __call__(self, state: dict) -> None:
        if not self.on:
            return
        now = time.monotonic()
        if now - self.last < 0.25:
            return
        self.last = now
        tot = state.get("total_s") or 0.0
        pct = f"{100 * state.get('done_s', 0) / tot:5.1f}%" if tot else "      "
        sys.stderr.write(f"\r{self.label}: {state.get('stage', ''):8s} {pct}   ")
        sys.stderr.flush()

    def done(self) -> None:
        if self.on:
            sys.stderr.write("\r" + " " * 70 + "\r")
            sys.stderr.flush()


def _registry(args) -> _instruments.Registry:
    return _instruments.load(args.config)


# --- commands ------------------------------------------------------------------------------------------------
def cmd_list(args) -> int:
    reg = _registry(args)
    rows = []
    for i in reg.ids():
        inst = reg.get(i)
        why = reg.why_unavailable(i)
        rows.append({"id": i, "label": inst.get("label", i), "engine": inst.get("engine"), "available": why is None,
                     "why": why, "default": i == reg.default, "room": inst.get("room"), "file": inst.get("file")})
    rooms = [{"id": r, "label": v.get("label", r), "ir": v.get("ir"), "synth": v.get("synth"),
              "wet_db": v.get("wet_db")} for r, v in sorted(reg.rooms.items())]
    if args.json:
        print(json.dumps({"config": str(reg.path), "root": str(reg.root), "engines_root": str(reg.engines_root),
                          "default": reg.default, "instruments": rows, "rooms": rooms}, indent=1))
        return 0
    print(f"config  {reg.path}{'' if reg.path and reg.path.exists() else '  (missing)'}")
    print(f"root    {reg.root}")
    print(f"engines {reg.engines_root}")
    if not rows:
        print("no instruments")
    for r in rows:
        mark = "*" if r["default"] else " "
        state = "ok" if r["available"] else f"-- {r['why']}"
        print(f"{mark} {r['id']:22s} {str(r['engine']):11s} {r['label']}  [{state}]")
    for r in rooms:
        print(f"  room {r['id']:17s} {r['label']}")
    return 0


def _result_line(res) -> str:
    s = res.stats or {}
    how = "cached" if res.cached else f"rendered in {s.get('wall_s', 0):.1f}s (cpu {s.get('cpu_s', 0):.1f}s)"
    return (f"{res.path}  {res.duration_s:.1f}s  {res.lufs if res.lufs is not None else '-inf'} LUFS  "
            f"TP {res.true_peak_dbtp} dBTP  {how}")


def default_out(midi: str, instrument: Optional[str], reg) -> Path:
    """render's file when neither --out nor a cache is named: "<piece> - <instrument>.flac" in the current folder."""
    return Path.cwd() / f"{Path(midi).stem} - {instrument or reg.default}.flac"


def cmd_render(args) -> int:
    from . import render as _render
    if not args.no_idle:
        lower_priority()
    reg = _registry(args)
    out = args.out
    if out is None and _render.cache_dir() is None:
        out = default_out(args.midi, args.instrument, reg)
    prog = _TTYProgress(Path(args.midi).name)
    try:
        res = _render.render(args.midi, args.instrument, room=args.room, realism=not args.no_realism, registry=reg,
                             progress=prog, force=args.force, out=out)
    finally:
        prog.done()
    if args.json:
        d = res.to_json()
        d["cached"] = res.cached
        print(json.dumps(d, indent=1))
    else:
        print(_result_line(res))
    return 0


def cmd_fetch(args) -> int:
    mod = optional_module("fetch", "fetch")
    if mod is None:
        return 2
    cfg = args.registry or args.config or os.environ.get(_instruments.CONFIG_ENV)
    argv = (["--registry", str(Path(cfg).expanduser())] if cfg else []) + list(args.ids) + \
        (["--status"] if args.status else [])
    if hasattr(mod, "main"):
        return int(mod.main(argv) or 0)
    if hasattr(mod, "fetch"):
        mod.fetch(list(args.ids) or None)
        return 0
    print("piano_render.fetch has neither main(argv) nor fetch(ids)", file=sys.stderr)
    return 2


def cmd_audition(args) -> int:
    from . import audition as _audition
    if not args.no_idle:
        lower_priority()
    rows = _audition.run(args.instruments, args.excerpts, out_dir=args.out, work_dir=args.work, ab=not args.no_ab,
                         registry=_registry(args),
                         log=(lambda s: None) if args.json else (lambda s: print(s, flush=True)))
    if args.json:
        print(json.dumps(rows, indent=1))
    return 0


def cmd_play_pc22(args) -> int:
    mod = optional_module("pc22", "play-pc22")
    if mod is None:
        return 2
    path = Path(args.path)
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    if hasattr(mod, "play"):
        info = mod.play(str(path), instrument=args.instrument, room=args.room, realism=not args.no_realism)
        print(json.dumps(info, indent=1, default=str))
        return 0 if not (isinstance(info, dict) and info.get("error")) else 1
    if path.suffix.lower() in (".mid", ".midi"):
        print("piano_render.pc22 has no play() to render a .mid as it plays", file=sys.stderr)
        return 2
    out = mod.PC22Output()
    t0 = out.start(str(path))
    print(json.dumps({"path": str(path), "t_audio0": t0}))
    try:
        out.wait()
    except KeyboardInterrupt:
        pass
    finally:
        out.stop()
    return 0


# --- parser --------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m piano_render", description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=None, help=f"instruments.json (default ${_instruments.CONFIG_ENV} or "
                                                    f"{_instruments.DEFAULT_CONFIG})")
    ap.add_argument("--cache", default=None, help="a render cache to keep renders in (default $PIANO_RENDER_CACHE; "
                                                  "none: nothing is kept)")
    ap.add_argument("--no-idle", action="store_true", help="keep normal CPU priority for render/audition")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="instruments and rooms")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    def render_opts(p, positional: str, nargs=None, help_=""):
        p.add_argument(positional, nargs=nargs, help=help_)
        p.add_argument("--instrument", "-i", default=None, help="instrument id (default: the registry's default)")
        p.add_argument("--room", default=None, help="room id, or 'none' for dry (default: the instrument's room)")
        p.add_argument("--no-realism", action="store_true", help="engine + master only")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("render", help="render one MIDI file offline")
    render_opts(p, "midi", help_="the .mid file")
    p.add_argument("--out", default=None, help="the FLAC to write (default: \"<piece> - <instrument>.flac\" here, "
                                               "unless a cache is named)")
    p.add_argument("--force", action="store_true", help="render again even if cached")
    p.set_defaults(fn=cmd_render)

    # fetch's own options, declared here (an argparse.REMAINDER passthrough rejected "fetch --status")
    p = sub.add_parser("fetch", help="install instruments, rooms and sfizz (piano_render.fetch)")
    p.add_argument("ids", nargs="*", help="instrument ids, room ids or 'sfizz' (default: everything)")
    p.add_argument("--status", action="store_true", help="only report what is installed (no network)")
    p.add_argument("--registry", default=None, help="instruments.json (default: --config)")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("audition", help="render the audition set (excerpts x instruments) as tagged 16-bit FLACs")
    p.add_argument("--out", default=None, help="folder for the FLACs (default PC22's Music/Piano audition)")
    p.add_argument("--instruments", "-i", nargs="+", default=None, help="ids (default: every installed one)")
    p.add_argument("--excerpts", nargs="+", type=int, default=None, help="excerpt numbers 1-5 (default: all)")
    p.add_argument("--no-ab", action="store_true", help="skip the default instrument's dry / no-room files")
    p.add_argument("--work", default=None, help="excerpts, renders and table.json (default "
                                                "~/.cache/screen-machine/piano/audition)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_audition)

    p = sub.add_parser("play-pc22", help="play a file, or a .mid rendered as it plays, on PC22's speakers")
    p.add_argument("path")
    p.add_argument("--instrument", "-i", default=None)
    p.add_argument("--room", default=None)
    p.add_argument("--no-realism", action="store_true")
    p.set_defaults(fn=cmd_play_pc22)
    return ap


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.config:
        os.environ[_instruments.CONFIG_ENV] = str(Path(args.config).expanduser().resolve())
    if args.cache:
        os.environ["PIANO_RENDER_CACHE"] = str(Path(args.cache).expanduser().resolve())
    from .engines import EngineError
    try:
        return args.fn(args)
    except (KeyError, FileNotFoundError, ValueError, EngineError, TimeoutError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else e
        print(f"piano_render: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
