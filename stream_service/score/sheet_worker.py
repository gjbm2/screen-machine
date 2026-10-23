"""Score pages on demand from a warm process (owner, 23 Oct: '"Finding the piece" takes *ages* ... and often fails').

The first version laid out a score by starting Python and Verovio afresh and drawing every page as SVG for each
size asked: Islamey on a phone is 165 pages, 11-34 s a size, past the page's 30 s wait.  This worker stays up
(show_service's score_sheet.py starts it with the show and keeps it), keeps the last LRU layouts (one Verovio
toolkit each: the MEI laid out for one page size and staff, and the pages it has drawn), and while no request waits
it draws the newest layout's pages ahead, from the one last asked for onwards (owner, 23 Oct: "user is playing the
piano -- needs to turn pages instantly with a tap": Islamey's pages took 0.2-0.4 s each to draw, and up to 14 s
queued behind a layout; drawn ahead, a turn is a lookup).

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
import select
import sys
from typing import Optional

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


class _Layout:
    """One size's Verovio toolkit and the pages it has drawn."""

    def __init__(self, tk) -> None:
        self.tk = tk
        self.count = tk.getPageCount()
        self.svg: dict[int, str] = {}

    def page(self, n: int) -> str:
        got = self.svg.get(n)
        if got is None:
            got = self.svg[n] = self.tk.renderToSVG(n)
        return got

    def next_undrawn(self, near: int) -> Optional[int]:
        """The page to draw ahead: the ones after ``near`` first (a reader goes forward), then the rest."""
        for n in list(range(near, self.count + 1)) + list(range(near - 1, 0, -1)):
            if n not in self.svg:
                return n
        return None


def _ready(f) -> bool:
    return bool(select.select([f], [], [], 0)[0])


def main() -> int:
    layouts: "collections.OrderedDict[tuple, _Layout]" = collections.OrderedDict()
    born = _mtimes()
    stdin = sys.stdin
    ahead: Optional[tuple] = None                        # the layout pages are drawn ahead for, and where from
    while True:
        # idle: draw the newest layout's pages ahead, one at a time, as long as no request is waiting
        while ahead is not None and not _ready(stdin):
            lay = layouts.get(ahead[0])
            n = lay.next_undrawn(ahead[1]) if lay is not None else None
            if n is None:
                ahead = None
                break
            try:
                lay.page(n)
            except Exception:                            # noqa: BLE001  a page that won't draw: asked for, it says so
                lay.svg[n] = ""
        line = stdin.readline()
        if not line:
            return 0
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
                lay = layouts.get(key)
                if lay is None:
                    k = STAFF_UNITS / max(12.0, staff)
                    lay = layouts[key] = _Layout(_toolkit(pathlib.Path(path).read_text(), w * k, h * k,
                                                          (50, 50, 50, 50), header=False))
                    while len(layouts) > LRU:
                        layouts.popitem(last=False)
                else:
                    layouts.move_to_end(key)
                if op == "count":
                    out = {"pages": lay.count}
                    ahead = (key, 1)
                else:
                    n = int(req["n"])
                    if not 1 <= n <= lay.count:
                        raise ValueError(f"no page {n} (of {lay.count})")
                    svg = lay.page(n)
                    if not svg:
                        raise RuntimeError(f"page {n} wouldn't draw")
                    out = {"svg": svg}
                    ahead = (key, n + 1)
            else:
                raise ValueError(f"unknown op {op!r}")
        except Exception as e:                           # noqa: BLE001  one answer per request, always
            out = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps({"id": rid, **out}) + "\n")
        sys.stdout.flush()
        if _mtimes() != born:
            return 0


if __name__ == "__main__":
    sys.exit(main())
