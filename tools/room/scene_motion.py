#!/usr/bin/env python3
"""Write config/live/scene_motion.generated.json: what each screen scene's picture does (its direction of travel,
pace and busyness), for the lights (show_service/picture.py). From the renderer's tags.generated.json (each preset's
``_look`` words) and look_stats.generated.json (the measured numbers). Run again after either is regenerated:

    .venv/bin/python tools/room/scene_motion.py [--print]

Correct a scene by hand in config/live/scene_motion.yaml (``scenes: {<preset>: {dir: inward}}``): that file wins.
"""
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from show_service import picture  # noqa: E402


def main() -> None:
    def load(p: pathlib.Path):
        return json.loads(p.read_text()) if p.exists() else None
    body = picture.build(load(picture.TAGS_FILE), load(picture.STATS_FILE))
    if "--print" not in sys.argv:
        picture.FACTS_FILE.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    c = collections.Counter(v["dir"] or "none" for v in body["scenes"].values())
    print(f"{len(body['scenes'])} scenes: " + ", ".join(f"{k} {n}" for k, n in c.most_common()))


if __name__ == "__main__":
    main()
