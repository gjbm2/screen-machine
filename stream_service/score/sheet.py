"""A whole piece's score, not in real time (owner, 23 Oct: "feed a midi that user has recorded (or selected from Music
tab) to our score generator and display in browser ... no animation, just a generated score, suitable for scrolling
or paginating"; then: "first pass can't take minutes to generate a score ... if there's a more sophisticated neural
analysis pass then user could request that on demand").

Two passes:
* **quick** (the default, ~1-2 s a piece): a sequenced file (its notes on its own beat grid) is read straight off
  that grid, bar lines, metre and key from the file (sheet_grid.py); a performance (no grid: the Steinway's
  recordings, most of the library) has its beat tracked once over the whole piece by dynamic programming and its
  metre read from the accents (sheet_beats.py).
* **better** (--neural, asked for on its own; ~25 ms of CPU a note: a minute or more for a long piece): the live
  score's networks (PM2S) read the whole piece once (sheet_analysis.py), and the piece is played into the live
  ScoreBuilder in simulated time, frame by frame as the screens feed it, its analysis requests answered from that
  one reading (a rewrite replaces the phrase's bars so far, as on the screens).
Either way the bars become one MEI document: every bar in the key it was written in, key and time changes inline,
ties across bar lines, dynamics, pedal lines, octave lines, a new system after each phrase.  Verovio lays it out in
pages of any size, or a PDF to print (sheet_pages.py).

    python -m stream_service.score.sheet make  <file.mid> <out.mei> [--neural] [--title T] [--composer C]
        progress as JSON lines on stdout: {"progress": 0-1}, then {"done": true, "bars": n, ...} or {"error": "..."}
    python -m stream_service.score.sheet pages <file.mei> --width PX --height PX [--staff PX]
        one JSON line: {"pages": ["<svg ...>", ...]}; pages are width x height css px with a staff --staff px high
    python -m stream_service.score.sheet pdf <file.mei> <out.pdf> [--paper a4|a4-landscape|letter] [--title --composer]
        one JSON line: {"done": true, "bytes": n}

make runs in sm-score-ml's python (numpy; torch for --neural), pages and pdf in sm-stream's (Verovio, Playwright);
show_service's score_sheet.py runs them and keeps the MEI.
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
from .sheet_pages import PAPERS, pages, pdf

FPS = 30.0
TAIL_S = 8.0                         # played on after the last event, so the last phrase ends and is written
LATENCY_S = 0.6                      # a reply is taken this long after its request (the screens' worker: 0.3-1.5 s)
SOURCES = ("sheet.py", "sheet_grid.py", "sheet_beats.py", "sheet_analysis.py", "quantize.py", "mei.py", "metre.py",
           "pm2s_net.py")


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
def transcribe(events: list, analyse: Optional[Callable[[dict], dict]] = None,
               progress: Optional[Callable[[float], None]] = None) -> list:
    """MIDI events [(t s, status, d1, d2)] -> [(Bar, fifths)] as a reader ends up with them (markers kept: a phrase
    ended there).  ``analyse``: answers the builder's analysis requests (sheet_analysis.WholePiece; None: the live
    fallback, a 4/4 beat clock); each answer is taken LATENCY_S after its request, as on the screens."""
    from stream_service.piano_replay import CC, NOTE_OFF, NOTE_ON
    sb = ScoreBuilder()
    if analyse is not None:
        sb.use_analysis(True)
    evs = sorted(events)
    if not evs:
        return []
    end = evs[-1][0] + TAIL_S
    out: list = []
    pending: collections.deque = collections.deque()      # (due, reply), in request order
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
        while pending and pending[0][0] <= t:
            rep = pending.popleft()[1]
            if rep.get("ok"):
                sb.on_analysis(rep)
        if analyse is not None:
            req = sb.analysis_request(t)
            if req is not None:
                pending.append((t + LATENCY_S, analyse(req)))
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


# -- the command line ---------------------------------------------------------------------------------------------
def _say(**kw) -> None:
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def _from_seconds(events: list):
    """A MIDI file with no beat grid of its own (SMPTE time) as a sheet_grid.MidiFile: a quarter a second."""
    from stream_service.piano_replay import CC, NOTE_OFF, NOTE_ON
    from .sheet_grid import MidiFile, Note
    mf = MidiFile(ppq=1000, tempos=[(0, 1_000_000)])
    held: dict = {}
    for te, st, d1, d2 in sorted(events):
        tick = int(round(te * 1000))
        if st == NOTE_ON and d2 > 0:
            held.setdefault(d1, []).append((tick, d2))
        elif st in (NOTE_ON, NOTE_OFF) and held.get(d1):
            on, v = held[d1].pop(0)
            mf.notes.append(Note(on, max(tick, on + 1), d1, v, (0, 0)))
        elif st == CC and d1 == 64:
            mf.pedal.append((tick, d2 >= 64))
    for k, n in enumerate(sorted(mf.notes, key=lambda n: (n.on, n.pitch))):
        n.id = k + 1
    mf.notes.sort(key=lambda n: n.on)
    return mf


def make(midi: pathlib.Path, out: pathlib.Path, title: str = "", composer: str = "", neural: bool = False) -> dict:
    from stream_service.piano_replay import read_midi
    from . import sheet_beats, sheet_grid
    t = time.monotonic()
    data = midi.read_bytes()
    if neural:
        from .sheet_analysis import WholePiece
        events = read_midi(data)
        whole = WholePiece(events, progress=lambda p: _say(progress=round(p, 3)))
        bars = transcribe(events, whole, progress=lambda p: _say(progress=round(0.8 + 0.2 * p, 3)))
        how = "neural"
    else:
        mf = sheet_grid.read_ticks(data) or _from_seconds(read_midi(data))
        if sheet_grid.sequenced(mf):
            bars, how = sheet_grid.bars(mf), "grid"
        else:
            bars, how = sheet_beats.bars(mf), "beats"
    music = [b for b, _ in bars if not b.marker]
    if not music:
        raise ValueError("no notes to write")
    mei = score_doc(bars, title=title, composer=composer)
    tmp = out.with_suffix(".part")
    tmp.write_text(mei)
    tmp.replace(out)
    return {"bars": len(music), "how": how, "s": round(time.monotonic() - t, 1)}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m stream_service.score.sheet")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("midi", type=pathlib.Path)
    m.add_argument("out", type=pathlib.Path)
    m.add_argument("--title", default="")
    m.add_argument("--composer", default="")
    m.add_argument("--neural", action="store_true")
    p = sub.add_parser("pages")
    p.add_argument("mei", type=pathlib.Path)
    p.add_argument("--width", type=float, required=True)
    p.add_argument("--height", type=float, required=True)
    p.add_argument("--staff", type=float, default=36.0)
    d = sub.add_parser("pdf")
    d.add_argument("mei", type=pathlib.Path)
    d.add_argument("out", type=pathlib.Path)
    d.add_argument("--paper", default="a4", choices=sorted(PAPERS))
    d.add_argument("--title", default="")
    d.add_argument("--composer", default="")
    sub.add_parser("version")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "make":
            _say(done=True, **make(a.midi, a.out, a.title, a.composer, a.neural))
        elif a.cmd == "pages":
            _say(pages=pages(a.mei.read_text(), a.width, a.height, a.staff))
        elif a.cmd == "pdf":
            data = pdf(a.mei.read_text(), a.paper, title=a.title, composer=a.composer)
            tmp = a.out.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(a.out)
            _say(done=True, bytes=len(data))
        else:
            _say(version=generator_version())
    except Exception as e:                                  # one line the caller can show
        _say(error=f"{type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
