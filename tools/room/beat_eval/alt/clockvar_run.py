#!/usr/bin/env python3
"""Per-track runner for run_batch.py ``--runner``: one audio file through a variant of OUR clock (alt/clockvar.py),
taking run_stack.py's own options and writing run_stack.py's own JSON (score.py scores it unchanged).

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py INDEX --track-seeds ... \\
      --runner tools/room/beat_eval/alt/clockvar_run.py -- --variant 'nets=8+b+a'

``--variant`` (default ``base``: the stock clock, i.e. run_stack.py itself) is clockvar.parse_variant's name. The
first run of a track (per its run_stack options) records the cache (clockvar_replay.py: madmom, dsp and the room
model once, the live path's own result), then replays the variant on it; later variants of the same track and
condition replay in a few seconds. run_batch.py already holds the CPU lock around this process (flock), so this
never takes it. ``--no-cache`` runs the variant on the live path instead (madmom and dsp computed, nothing kept).
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clockvar as cv  # noqa: E402
import clockvar_replay as cr  # noqa: E402


def main(argv: list[str]) -> int:
    variant, use_cache = "base", True
    rest: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--variant":
            variant, i = argv[i + 1], i + 2
        elif argv[i].startswith("--variant="):
            variant, i = argv[i].split("=", 1)[1], i + 1
        elif argv[i] == "--no-cache":
            use_cache, i = False, i + 1
        else:
            rest.append(argv[i])
            i += 1
    spec = cv.parse_variant(variant)
    if variant in ("", "base") and spec["nets"] == 2:
        if not use_cache:
            return cr.run_stack.main(rest)
    out = pathlib.Path(rest[rest.index("--out") + 1])
    if not use_cache:                                    # the live path: madmom + dsp, the variant's clock
        with tempfile.TemporaryDirectory(prefix="clockvar-") as td:
            cp = pathlib.Path(td) / "c.npz"
            cr.record([*rest[:rest.index("--out")], "--out", str(pathlib.Path(td) / "base.json"),
                       *rest[rest.index("--out") + 2:]], cp)
            cache = cv.ensure_cache(rest, cp, out, [cv.ced_norm(spec)] if cv.needs_ced(spec) else [], _seed(rest))
            if spec["nets"] != 2:
                cr.record_act(rest, cache, spec["nets"])
            cv.replay_variant(rest, cache, spec)
        return 0
    cp = cr.cache_path(rest)
    if not cp.exists():
        base_out = cv.res_dir("runner", "x", "base") / out.name
        base_out.parent.mkdir(parents=True, exist_ok=True)
        cr.record([*rest[:rest.index("--out")], "--out", str(base_out), *rest[rest.index("--out") + 2:]], cp)
    cache = cv.ensure_cache(rest, cp, out, [cv.ced_norm(spec)] if cv.needs_ced(spec) else [], _seed(rest))
    if variant in ("", "base") and spec["nets"] == 2:
        spec = cv.parse_variant("stock")
    if spec["nets"] != 2 and not cache.side(f"act{spec['nets']}.npz").exists():
        cr.record_act(rest, cache, spec["nets"])
    cv.replay_variant(rest, cache, spec)
    print(f"-> {out}")
    return 0


def _seed(argv: list[str]) -> int:
    return int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
