#!/usr/bin/env python3
"""The DEV subset for tuning beat-stack candidates: the baseline's datasets and genre spread, none of its tracks.

baseline-subset.json is the held-out test (never tune on it). Candidates (alt/*.py) are tuned on this one instead:
the same dealer as ``baseline.py subset`` (round-robin over each dataset's genres, biggest first), another seed, and
every baseline track left out, so the two sets share datasets and genres but no audio.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/dev_subset.py [--seed 1] [--counts ...]

Idempotent and shared: the first builder that needs it writes ~/.cache/screen-machine/beat-eval/dev-subset.json
under flock ~/.cache/screen-machine/dev-subset.lock; later calls (and other builders) find it there and keep it.
``--force`` rewrites it (only if nobody has results on it yet).
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import baseline  # noqa: E402
import run_batch  # noqa: E402

CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval").expanduser()
DEV = CACHE / "dev-subset.json"
LOCK = pathlib.Path("~/.cache/screen-machine/dev-subset.lock").expanduser()
COUNTS = "ballroom=20,gtzan=20,harmonix=6,tapcorrect=2,asap=3,smc=4"
SEED = 1


def build(counts: dict[str, int], seed: int) -> dict:
    test = json.loads(baseline.SUBSET.read_text())
    held = {(i["dataset"], i["id"]) for i in test["items"]}
    _, entries = run_batch.load_index(baseline.COMBINED)
    entries = [e for e in entries if not e["duplicate"] and (e["dataset"], e["id"]) not in held]
    items, missing = [], []
    for ds, n in counts.items():
        have = [e for e in entries if e["dataset"] == ds and e["audio"] and pathlib.Path(e["audio"]).exists()]
        if not have:
            missing.append(ds)
            continue
        for e in baseline.deal(have, n, seed):
            items.append({"id": e["id"], "dataset": ds, "genre": e["genre"], "audio": str(e["audio"]),
                          "beats": str(e["beats"]), "duration_s": e.get("duration_s")})
    assert not ({(i["dataset"], i["id"]) for i in items} & held), "dev subset overlaps the held-out test"
    return {"name": "dev", "seed": seed, "counts": counts, "missing": missing, "excludes": str(baseline.SUBSET),
            "items": items}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--counts", default=COUNTS)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if DEV.exists() and not a.force:
            doc = json.loads(DEV.read_text())
            print(f"{DEV} is already there (seed {doc.get('seed')}, {len(doc.get('items', []))} tracks): kept")
            return 0
        counts = {k.strip(): int(v) for k, v in (p.split("=") for p in a.counts.split(",") if p.strip())}
        doc = build(counts, a.seed)
        tmp = DEV.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(DEV)
    by = collections.Counter(i["dataset"] for i in doc["items"])
    print(f"{len(doc['items'])} tracks -> {DEV}: {dict(by)}"
          + (f"; not available: {', '.join(doc['missing'])}" if doc["missing"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
