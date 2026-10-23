"""Score pages on demand from a warm process (owner, 23 Oct: '"Finding the piece" takes *ages* ... and often fails').

The first version laid out a score by starting Python and Verovio afresh and drawing every page as SVG for each
size asked: Islamey on a phone is 165 pages, 11-34 s a size, past the page's 30 s wait.  This worker stays up
(show_service's score_sheet.py starts it with the show and keeps it), keeps the last LRU layouts (one Verovio
toolkit each: the MEI laid out for one page size and staff, a few pages at a time: sheet_pages.Paginator, so a
count answers once the first pages are laid out, with an estimate, and the rest follow), and while no request waits
it lays out the rest of the newest layout, then draws its pages ahead, from the one last asked for onwards (owner, 23 Oct: "user is playing the
piano -- needs to turn pages instantly with a tap": Islamey's pages took 0.2-0.4 s each to draw, and up to 14 s
queued behind a layout; drawn ahead, a turn is a lookup).

    python -m stream_service.score.sheet_worker       (sm-stream's python: Verovio)
stdin / stdout, one JSON object a line each way:
    {"id", "op": "version"}                                    -> {"id", "version"}
    {"id", "op": "count", "mei": path, "w", "h", "staff"}       -> {"id", "pages", "partial"} (partial: an estimate)
    {"id", "op": "page", "mei": path, "w", "h", "staff", "n"}   -> {"id", "svg"}
    {"id", "op": "geometry", ... same, "n"}                    -> {"id", "bars": [[n, x0, y0, x1, y1]], "heads": [[id, x, y]]}
    {"id", "op": "pagemap", ... same as count}                 -> {"id", "pages": [[first bar, last bar], ...]}
    {"id", "op": "timemap", "mei", "midi", "kind"}             -> {"id", "bars": [[n, t0, t1]], "notes": [[id, t, n]]}
    anything failing                                           -> {"id", "error"}
It exits after answering once sheet_pages.py has changed on disk (the next request starts a fresh one).
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import select
import sys
from typing import Optional

from .sheet import generator_version
from .sheet_pages import STAFF_UNITS, Paginator

LRU = 6
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


_MEASURE = re.compile(r'<measure xml:id="m(\d+)"')
_FIRST = re.compile(r'<note xml:id="n(\d+)-0"')
_SVG_MEASURE = re.compile(r'<g id="m(\d+)" class="measure')


def timemap(mei_path: str, midi_path: str, kind: str) -> dict:
    """When each bar and note of a written score sounds, in the MIDI's own seconds (the player's pos_s), read back
    from the MEI and the MIDI alone: a note's id is the MIDI note it was written from (the quick pass numbers the
    notes by onset and pitch, sheet_grid.read_ticks; the better pass in the order they were played into the
    ScoreBuilder), so scores written before the timemap existed follow along too.
    -> {"bars": [[n, t0, t1], ...], "notes": [[id, t, n], ...] by t}.  A bar starts at its first note's onset (a bar
    with none: between its neighbours) and ends where the next starts."""
    from stream_service.piano_replay import NOTE_ON, read_midi
    from .sheet_grid import read_ticks, seconds_of
    data = pathlib.Path(midi_path).read_bytes()
    onset: dict[int, float] = {}
    if kind == "better":
        k = 0
        for t, st, _d1, d2 in sorted(read_midi(data)):
            if st == NOTE_ON and d2 > 0:
                k += 1
                onset[k] = t
    else:
        mf = read_ticks(data)
        if mf is None:
            from .sheet import _from_seconds
            mf = _from_seconds(read_midi(data))
        sec = seconds_of(mf)
        onset = {n.id: sec(n.on) for n in mf.notes}
    text = pathlib.Path(mei_path).read_text()
    marks = [(m.start(), int(m.group(1))) for m in _MEASURE.finditer(text)] + [(len(text), -1)]
    bars: list = []
    notes: list = []
    for (a, n), (b, _) in zip(marks, marks[1:]):
        ts = []
        for m in _FIRST.finditer(text, a, b):
            nid = int(m.group(1))
            if nid in onset:
                ts.append(onset[nid])
                notes.append([nid, round(onset[nid], 3), n])
        bars.append([n, min(ts) if ts else None])
    known = [i for i, (_, t) in enumerate(bars) if t is not None]
    for i, (n, t) in enumerate(bars):                    # a bar with no note of its own: between its neighbours
        if t is None:
            lo = max((j for j in known if j < i), default=None)
            hi = min((j for j in known if j > i), default=None)
            if lo is not None and hi is not None:
                bars[i][1] = bars[lo][1] + (bars[hi][1] - bars[lo][1]) * (i - lo) / (hi - lo)
            elif lo is not None:
                bars[i][1] = bars[lo][1] + (i - lo) * 1.0
            else:
                bars[i][1] = 0.0
    end = max(onset.values(), default=0.0) + 1.0
    out = []
    for i, (n, t) in enumerate(bars):
        t1 = bars[i + 1][1] if i + 1 < len(bars) else max(end, t + 0.5)
        out.append([n, round(t, 3), round(max(t1, t), 3)])
    notes.sort(key=lambda x: x[1])
    return {"bars": out, "notes": notes}


def geometry(svg: str, w: float, h: float) -> dict:
    """A drawn page's bars (their boxes: the bar's width by its system's staves) and note heads, as fractions of the
    page: {"bars": [[n, x0, y0, x1, y1], ...], "heads": [[id, x, y], ...]} (engrave.py's own SVG reading)."""
    from .engrave import _geometry, heads
    ms, inner, k, ox, oy = _geometry(svg, w)
    bars, space = [], None
    for m in ms:
        try:
            n = int(str(m["id"]).lstrip("m"))
        except ValueError:
            continue
        top = min(s[0] for s in m["staves"])
        bot = max(s[1] for s in m["staves"])
        if space is None and m["staves"]:
            space = (m["staves"][0][1] - m["staves"][0][0]) / 4
        bars.append([n, round(m["x0"] / w, 4), round(top / h, 4), round(m["x1"] / w, 4), round(bot / h, 4)])
    hs = heads(inner, k, ox, oy, space or 8.0)
    return {"bars": bars, "heads": [[i, round(x / w, 4), round(y / h, 4)] for i, x, y in hs], "space": round((space or 8.0) / h, 4)}


class _Layout:
    """One size's layout, laid out a few pages at a time (sheet_pages.Paginator), and the pages it has drawn."""

    def __init__(self, pg) -> None:
        self.pg = pg
        self.svg: dict[int, str] = {}
        self.geo: dict[int, dict] = {}

    @property
    def count(self) -> int:
        return self.pg.estimate

    @property
    def done(self) -> bool:
        return self.pg.done

    def pagemap(self) -> list:
        """[[first bar, last bar], ...] for each page laid out so far."""
        return [[a, z] for _tk, _p, a, z in self.pg.pages]

    def page(self, n: int) -> str:
        got = self.svg.get(n)
        if got is None:
            self.pg.upto(n)
            if n > len(self.pg.pages):
                raise ValueError(f"no page {n} (of {len(self.pg.pages)})")
            tk, p, _a, _z = self.pg.pages[n - 1]
            got = self.svg[n] = tk.renderToSVG(p)
        return got

    def next_undrawn(self, near: int) -> Optional[int]:
        """The page to draw ahead: the ones after ``near`` first (a reader goes forward), then the rest."""
        have = len(self.pg.pages)
        for n in list(range(near, have + 1)) + list(range(near - 1, 0, -1)):
            if n not in self.svg:
                return n
        return None


def _ready(f) -> bool:
    return bool(select.select([f], [], [], 0)[0])


def main() -> int:
    layouts: "collections.OrderedDict[tuple, _Layout]" = collections.OrderedDict()
    maps: dict = {}
    born = _mtimes()
    stdin = sys.stdin
    ahead: Optional[tuple] = None                        # the layout pages are drawn ahead for, and where from
    while True:
        # idle: draw the newest layout's pages ahead, one at a time, as long as no request is waiting
        while ahead is not None and not _ready(stdin):
            lay = layouts.get(ahead[0])
            n = lay.next_undrawn(ahead[1]) if lay is not None else None
            if lay is not None and not lay.done and (n is None or n >= ahead[1] + 3):
                lay.pg.more()                             # the next pages drawn first, then the rest laid out
                continue
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
            elif op == "timemap":
                key = (str(req["mei"]), os.stat(str(req["mei"])).st_mtime_ns, "timemap")
                got = maps.get(key)
                if got is None:
                    got = maps[key] = timemap(str(req["mei"]), str(req["midi"]), str(req.get("kind") or "quick"))
                    while len(maps) > 8:
                        maps.pop(next(iter(maps)))
                out = got
            elif op in ("count", "page", "geometry", "pagemap"):
                path = str(req["mei"])
                w, h, staff = float(req["w"]), float(req["h"]), float(req["staff"])
                key = (path, os.stat(path).st_mtime_ns, w, h, staff)
                lay = layouts.get(key)
                if lay is None:
                    k = STAFF_UNITS / max(12.0, staff)
                    lay = layouts[key] = _Layout(Paginator(pathlib.Path(path).read_text(), w * k, h * k,
                                                           (50, 50, 50, 50), header=False))
                    lay.pg.upto(2)
                    while len(layouts) > LRU:
                        layouts.popitem(last=False)
                else:
                    layouts.move_to_end(key)
                if op == "count":
                    out = {"pages": lay.count, "partial": not lay.done}
                    ahead = (key, 1)
                elif op == "pagemap":                    # each page's first and last bar, as far as laid out
                    out = {"pages": lay.pagemap(), "partial": not lay.done}
                elif op == "geometry":
                    n = int(req["n"])
                    if n < 1:
                        raise ValueError(f"no page {n}")
                    g = lay.geo.get(n)
                    if g is None:
                        g = lay.geo[n] = geometry(lay.page(n), w, h)
                    out = g
                else:
                    n = int(req["n"])
                    if n < 1:
                        raise ValueError(f"no page {n}")
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
