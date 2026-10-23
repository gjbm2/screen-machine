"""Score pages on demand from a warm process (owner, 23 Oct: '"Finding the piece" takes *ages* ... and often fails').

The first version laid out a score by starting Python and Verovio afresh and drawing every page as SVG for each
size asked: Islamey on a phone is 165 pages, 11-34 s a size, past the page's 30 s wait.  This worker stays up
(show_service's score_sheet.py starts it on the first score and keeps it), keeps the last LRU layouts (one Verovio
toolkit each: the MEI laid out for one page size and staff), counts a layout's pages once and draws a page only when
a page is asked for (~20-100 ms).

    python -m stream_service.score.sheet_worker       (sm-stream's python: Verovio)
stdin / stdout, one JSON object a line each way:
    {"id", "op": "version"}                                    -> {"id", "version"}
    {"id", "op": "count", "mei": path, "w", "h", "staff"}       -> {"id", "pages"}
    {"id", "op": "page", "mei": path, "w", "h", "staff", "n"}   -> {"id", "svg"}
    anything failing                                           -> {"id", "error"}
It exits after answering once sheet_pages.py has changed on disk (the next request starts a fresh one).
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import sys

from .sheet import generator_version
from .sheet_pages import STAFF_UNITS, _toolkit

LRU = 4
HERE = pathlib.Path(__file__).parent
WATCH = (HERE / "sheet_pages.py", HERE / "sheet_worker.py")


def _mtimes() -> tuple:
    out = []
    for p in WATCH:
        try:
            out.append(p.stat().st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


def main() -> int:
    tks: "collections.OrderedDict[tuple, object]" = collections.OrderedDict()
    born = _mtimes()
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid = req.get("id")
        try:
            op = req.get("op")
            if op == "version":
                out: dict = {"version": generator_version()}
            elif op in ("count", "page"):
                path = str(req["mei"])
                w, h, staff = float(req["w"]), float(req["h"]), float(req["staff"])
                key = (path, os.stat(path).st_mtime_ns, w, h, staff)
                tk = tks.get(key)
                if tk is None:
                    k = STAFF_UNITS / max(12.0, staff)
                    tk = _toolkit(pathlib.Path(path).read_text(), w * k, h * k, (50, 50, 50, 50), header=False)
                    tks[key] = tk
                    while len(tks) > LRU:
                        tks.popitem(last=False)
                else:
                    tks.move_to_end(key)
                count = tk.getPageCount()
                if op == "count":
                    out = {"pages": count}
                else:
                    n = int(req["n"])
                    if not 1 <= n <= count:
                        raise ValueError(f"no page {n} (of {count})")
                    out = {"svg": tk.renderToSVG(n)}
            else:
                raise ValueError(f"unknown op {op!r}")
        except Exception as e:                           # noqa: BLE001  one answer per request, always
            out = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps({"id": rid, **out}) + "\n")
        sys.stdout.flush()
        if _mtimes() != born:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
