"""A whole piece's score, not in real time (owner, 23 Oct: "feed a midi that user has recorded (or selected from Music
tab) to our score generator and display in browser ... no animation, just a generated score, suitable for scrolling
or paginating").

The same generator as the live score: the MIDI is played into a ScoreBuilder in simulated time, frame by frame as the
live scene feeds it, with the real analysis worker (client.Analyser: PM2S + metre.py, ~/.venvs/sm-score-ml, nice 19
on the host's last 4 CPUs) answering each window.  The bars it writes (a rewrite replaces the phrase's bars so far, as
on the screens) become one MEI document: every bar in the key it was written in, key and time changes inline, ties
across bar lines, dynamics, pedal marks, octave lines, a new system after each phrase.  Verovio lays it out in pages
of any size.

    python -m stream_service.score.sheet make  <file.mid> <out.mei> [--title T] [--composer C]
        progress as JSON lines on stdout: {"progress": 0-1}, then {"done": true, "bars": n, ...} or {"error": "..."}
    python -m stream_service.score.sheet pages <file.mei> --width PX --height PX [--staff PX]
        one JSON line: {"pages": ["<svg ...>", ...]}; pages are width x height css px with a staff --staff px high

Run by sm-stream's python (Verovio); show_service's score_sheet.py runs both and keeps the MEI.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
import time
from typing import Callable, Optional
from xml.sax.saxutils import escape

from .mei import _keysig, _layer, _octave_xml, _tstamp, signature
from .quantize import Bar, ScoreBuilder

FPS = 30.0
TAIL_S = 8.0                         # played on after the last event, so the last phrase ends and is written
LATENCY_S = 0.6                      # a reply is taken this long after its request (the screens' worker: 0.3-1.5 s)
WORKERS = 2                          # analysis workers (2 threads, ~0.5 GB each; on the host's last 4 CPUs)
STAFF_UNITS = 72.0                   # a staff's height in Verovio units at scale 100 (4 spaces of 2 x unit 9)
SOURCES = ("sheet.py", "quantize.py", "mei.py", "metre.py", "analyse.py", "pm2s_net.py")


def generator_version() -> str:
    """Changes whenever the score generator's code does (a cached score is made again)."""
    h = hashlib.sha1()
    here = pathlib.Path(__file__).parent
    for name in SOURCES:
        try:
            h.update((here / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:12]


# -- the live score's input loop, in simulated time ---------------------------------------------------------------
def transcribe(events: list, analysers=(), progress: Optional[Callable[[float], None]] = None) -> list:
    """MIDI events [(t s, status, d1, d2)] -> [(Bar, fifths)] as a reader ends up with them (markers kept: a phrase
    ended there).  ``analysers``: client.Analyser workers, used in turn (none: the fallback 4/4 clock).  Each reply
    is taken LATENCY_S after its request, as on the screens, so two workers answer two windows at once."""
    from stream_service.piano_replay import CC, NOTE_OFF, NOTE_ON
    analysers = [a for a in analysers if a is not None]
    sb = ScoreBuilder()
    if analysers:
        sb.use_analysis(True)
    evs = sorted(events)
    if not evs:
        return []
    end = evs[-1][0] + TAIL_S
    out: list = []
    pending: collections.deque = collections.deque()      # [due, worker, reply or None], in request order

    def collect(e: list) -> None:
        if e[2] is None:
            e[2] = e[1].out.get(timeout=60)[1]

    def deliver(upto: float) -> None:
        while pending and pending[0][0] <= upto:
            e = pending.popleft()
            collect(e)
            if e[2].get("ok"):
                sb.on_analysis(e[2])

    i, t, frame, last_p = 0, 0.0, 0, -1.0
    while t <= end:
        while i < len(evs) and evs[i][0] <= t:
            te, st, d1, d2 = evs[i]
            if st == NOTE_ON and d2 > 0:
                sb.note_on(d1, d2 / 127.0, te)
            elif st in (NOTE_ON, NOTE_OFF):
                sb.note_off(d1, te)
            elif st == CC and d1 == 64:
                sb.pedal(d2 / 127.0, te)
            i += 1
        deliver(t)
        live = [a for a in analysers if a.ok]
        if live:
            req = sb.analysis_request(t)
            if req is not None:
                busy = {id(e[1]) for e in pending if e[2] is None}
                free = [a for a in live if id(a) not in busy]
                if not free:                                  # every worker answering: wait for the oldest
                    collect(next(e for e in pending if e[2] is None))
                    busy = {id(e[1]) for e in pending if e[2] is None}
                    free = [a for a in live if id(a) not in busy]
                free[0].submit("a", req)
                pending.append([t + LATENCY_S, free[0], None])
        for bar in sb.poll(t):
            if bar.rewrite:
                while out and not out[-1][0].marker:
                    out.pop()
                continue
            out.append((bar, sb.key()[2]))
        frame += 1
        t = frame / FPS
        if progress is not None and t / end - last_p >= 0.01:
            last_p = t / end
            progress(min(1.0, last_p))
    return out


# -- one MEI document for the whole piece -------------------------------------------------------------------------
def _staff_grp() -> str:
    return ('<staffGrp symbol="brace" bar.thru="true">'
            '<staffDef n="1" lines="5" clef.shape="G" clef.line="2"/>'
            '<staffDef n="2" lines="5" clef.shape="F" clef.line="4"/>'
            "</staffGrp>")


LEVELS = ("pp", "p", "mp", "mf", "f", "ff")
DYN_HOLD = 2                         # a new dynamic is marked only once it holds this many bars (the live score
                                     # marks each bar's change: on paper p pp p pp is noise)


def _dynamics(music: list) -> list:
    """Each bar's dynamic mark for the page, or None: the bars' levels (a mark holds until the next), a change
    kept only when it lasts DYN_HOLD bars."""
    level, levels = None, []
    for b in music:
        level = b.dynamic if b.dynamic in LEVELS else level
        levels.append(level)
    out, shown = [None] * len(music), None
    for i, lv in enumerate(levels):
        if lv is None or lv == shown:
            continue
        if shown is None or all(x == lv for x in levels[i:i + DYN_HOLD]):
            out[i], shown = lv, lv
    return out


def score_doc(bars: list, *, title: str = "", composer: str = "", pedal: bool = True) -> str:
    """[(Bar, fifths)] (transcribe()'s) -> MEI 4.  Markers end a phrase: the next bar starts a new system."""
    music = [(b, f) for b, f in bars if not b.marker]
    dyn = _dynamics([b for b, _ in music])
    sig0 = music[0][1] if music else 0
    meter0 = music[0][0].meter if music else (4, 4)
    pieces: dict[int, int] = {}
    body: list[str] = []
    fifths, meter, n, brk = sig0, meter0, 0, False
    for j, (b, f) in enumerate(bars):
        if b.marker:
            brk = n > 0
            continue
        change = []
        if f != fifths:
            change.append(f'keysig="{_keysig(f)}"')
        if b.meter != meter:
            change.append(f'meter.count="{b.meter[0]}" meter.unit="{b.meter[1]}"')
        if brk:
            body.append("<sb/>")
            brk = False
        if change:
            body.append(f"<scoreDef {' '.join(change)}/>")
        fifths, meter = f, b.meter
        sig = signature(f)
        first, last = n == 0, b is music[-1][0]
        octs: list = [[], []]
        staves = "".join(
            f'<staff n="{s + 1}"><layer n="1">'
            f"{_layer(b.staves[s], f, sig, pieces, open_in=not first, open_out=not last, units=b.units, unit=b.unit, staff=s, octaves=octs[s])}"
            "</layer></staff>" for s in (0, 1))
        ctrl = _octave_xml(octs[0], 1) + _octave_xml(octs[1], 2)
        if dyn[n]:
            ctrl += f'<dynam staff="1" tstamp="1" place="below">{dyn[n]}</dynam>'
        for pos, what in (b.pedal if pedal else ()):            # drawn as pedal lines (pages())
            ctrl += f'<pedal staff="2" tstamp="{_tstamp(b, pos):.3f}" dir="{what}" place="below"/>'
        n += 1
        end = ' right="end"' if last else ""
        body.append(f'<measure xml:id="m{n}" n="{n}"{end}>{staves}{ctrl}</measure>')
    head = (f'<scoreDef meter.count="{meter0[0]}" meter.unit="{meter0[1]}" keysig="{_keysig(sig0)}">'
            f"{_staff_grp()}</scoreDef>")
    t = f"<title>{escape(title)}</title>" if title else "<title/>"
    c = f"<composer>{escape(composer)}</composer>" if composer else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<mei xmlns="http://www.music-encoding.org/ns/mei" meiversion="4.0.1">'
            f"<meiHead><fileDesc><titleStmt>{t}{c}</titleStmt><pubStmt/></fileDesc></meiHead>"
            f"<music><body><mdiv><score>{head}<section>{''.join(body)}</section></score></mdiv></body></music></mei>")


# -- pages --------------------------------------------------------------------------------------------------------
def pages(mei: str, width_px: float, height_px: float, staff_px: float = 36.0) -> list[str]:
    """The score laid out as pages ``width_px`` x ``height_px`` (css px) with a staff ``staff_px`` high: SVGs that
    scale to that box (viewBox)."""
    import verovio
    k = STAFF_UNITS / max(12.0, staff_px)                  # Verovio units (scale 100) per css px
    tk = verovio.toolkit()
    tk.setOptions({"scale": 100, "pageWidth": int(max(400, width_px * k)), "pageHeight": int(max(400, height_px * k)),
                   "pageMarginLeft": 50, "pageMarginRight": 50, "pageMarginTop": 50, "pageMarginBottom": 50,
                   "adjustPageHeight": False, "breaks": "smart", "breaksSmartSb": 0.0, "header": "auto",
                   "footer": "none", "svgViewBox": True, "font": "Leipzig", "justifyVertically": True,
                   "pedalStyle": "line"})
    if not tk.loadData(mei):
        raise ValueError("Verovio couldn't read the score")
    return [tk.renderToSVG(p) for p in range(1, tk.getPageCount() + 1)]


# -- the command line ---------------------------------------------------------------------------------------------
def _say(**kw) -> None:
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def make(midi: pathlib.Path, out: pathlib.Path, title: str = "", composer: str = "") -> dict:
    from stream_service.piano_replay import read_midi
    from .client import Analyser
    t = time.monotonic()
    events = read_midi(midi.read_bytes())
    workers = [Analyser() for _ in range(WORKERS)] if Analyser.available() else []
    try:
        bars = transcribe(events, workers, progress=lambda p: _say(progress=round(p, 3)))
    finally:
        for w in workers:
            w.close()
    music = [b for b, _ in bars if not b.marker]
    if not music:
        raise ValueError("no notes to write")
    mei = score_doc(bars, title=title, composer=composer)
    tmp = out.with_suffix(".part")
    tmp.write_text(mei)
    tmp.replace(out)
    return {"bars": len(music), "analysed": bool(workers), "s": round(time.monotonic() - t, 1)}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stream_service.score.sheet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("midi", type=pathlib.Path)
    m.add_argument("out", type=pathlib.Path)
    m.add_argument("--title", default="")
    m.add_argument("--composer", default="")
    p = sub.add_parser("pages")
    p.add_argument("mei", type=pathlib.Path)
    p.add_argument("--width", type=float, required=True)
    p.add_argument("--height", type=float, required=True)
    p.add_argument("--staff", type=float, default=36.0)
    sub.add_parser("version")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "make":
            _say(done=True, **make(a.midi, a.out, a.title, a.composer))
        elif a.cmd == "pages":
            _say(pages=pages(a.mei.read_text(), a.width, a.height, a.staff))
        else:
            _say(version=generator_version())
    except Exception as e:                                  # one line the caller can show
        _say(error=f"{type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
