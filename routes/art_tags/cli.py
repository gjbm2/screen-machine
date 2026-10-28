"""Art tags from the shell, for calibration (docs/ART_TAGS.md). Run from the repo root with Flask's venv:

    python -m routes.art_tags.cli tag north-screen 20261027-223029-613e3305.jpg            # print, write nothing
    python -m routes.art_tags.cli tag north-screen 20261027-223029-613e3305.jpg --model gemini-3.8-flash
    python -m routes.art_tags.cli tag north-screen 20261027-223029-613e3305.jpg --write    # into the sidecar
    python -m routes.art_tags.cli sweep --dry                                               # counts per bucket
    python -m routes.art_tags.cli director --dry           # what the art director would do now (live GETs only)

One tag call is one paid Gemini call (about $0.001). The queue itself runs only inside Flask."""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m routes.art_tags.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tag", help="tag one bucket file and print the cleaned answer")
    t.add_argument("bucket")
    t.add_argument("filename")
    t.add_argument("--model", help="one model id (or provider/id) instead of llm.yaml's tag task")
    t.add_argument("--write", action="store_true", help="also write the answer into the sidecar")
    t.add_argument("--prompt", action="store_true", help="print the system prompt and the context text too")
    s = sub.add_parser("sweep", help="what a sweep would queue")
    s.add_argument("--dry", action="store_true", help="only count (the queue runs inside Flask)")
    dr = sub.add_parser("director", help="the art director's decision now, from the live room and show")
    dr.add_argument("--dry", action="store_true", help="decide only: no prompt call, no generation (the director "
                                                       "itself runs inside Flask)")
    a = ap.parse_args(argv)

    from routes.art_tags import lexicon, sidecar
    if a.cmd == "tag":
        from routes.art_tags.tagger import context_text, tag_file
        path = sidecar.media_path(a.bucket, a.filename)
        if not path.is_file():
            print(f"no such file: {path}", file=sys.stderr)
            return 2
        if a.prompt:
            print(lexicon.system_prompt(), "\n---")
            print(context_text(sidecar.read(path), a.bucket, int(lexicon.settings()["prompt_chars"])), "\n---")
        res = tag_file(a.bucket, a.filename, model=a.model)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if res.get("ok") and a.write:
            d = sidecar.update(path, lambda d: sidecar.apply_ai(d, res["ai"], res.get("model"), res.get("cost_usd"),
                                                                lexicon.load()["version"]))
            print(f"written: tags {d['tags']}", file=sys.stderr)
        return 0 if res.get("ok") else 1

    if a.cmd == "director":
        if not a.dry:
            print("the art director runs inside Flask: GET /api/art-tags/director (this only decides: --dry)",
                  file=sys.stderr)
            return 2
        from routes.art_tags.director import Director
        print(json.dumps(Director().pass_once(dry=True), indent=2, ensure_ascii=False))
        return 0

    if not a.dry:
        print("the sweep runs inside Flask: POST /api/art-tags/sweep (this only counts: --dry)", file=sys.stderr)
        return 2
    from routes.art_tags.worker import scan
    counts: dict = {}
    for r in scan():
        c = counts.setdefault(r["bucket"], {"total": 0, "done": 0, "failed": 0, "would_queue": 0})
        c["total"] += 1
        if r["status"] in ("done", "failed"):
            c[r["status"]] += 1
        else:
            c["would_queue"] += 1
    for b, c in counts.items():
        print(f"{b:28s} total {c['total']:5d}  done {c['done']:5d}  failed {c['failed']:4d}  "
              f"would queue {c['would_queue']:5d}")
    print(f"{'all':28s} would queue {sum(c['would_queue'] for c in counts.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
