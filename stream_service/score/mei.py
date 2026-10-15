"""Bars (quantize.py) -> one grand-staff system as MEI 4 for Verovio.

Spelling follows the key signature: diatonic notes take the key's letters, chromatic ones sharps in sharp keys
and flats in flat keys; accidentals are shown once per letter and octave in a bar (later ones are gestural) and a
natural cancels.  Notes are tied across pieces and bar lines, eighths and shorter are beamed within each beat,
dynamics go above the treble staff and pedal marks below the bass.  Unfilled slots in the system are invisible
spaces, so the bars already shown keep roughly their place as the system fills.  Every note's xml:id is
``n<note id>-<piece>``, so a renderer can find where a played note was engraved.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from .quantize import BAR, GRID, Bar, Event

SHARPS = ("c", "c", "d", "d", "e", "f", "f", "g", "g", "a", "a", "b")
SHARP_ALT = (0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0)
FLATS = ("c", "d", "d", "e", "e", "f", "g", "g", "a", "a", "b", "b")
FLAT_ALT = (0, -1, 0, -1, 0, 0, -1, 0, -1, 0, -1, 0)
ORDER_SHARP = "fcgdaeb"
ORDER_FLAT = "beadgcf"
LETTER_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
DUR = {1: ("16", 0), 2: ("8", 0), 3: ("8", 1), 4: ("4", 0), 6: ("4", 1), 8: ("2", 0), 12: ("2", 1), 16: ("1", 0)}


def signature(fifths: int) -> dict[str, int]:
    """Letter -> alteration in the key signature."""
    sig = {c: 0 for c in "cdefgab"}
    if fifths > 0:
        for c in ORDER_SHARP[:fifths]:
            sig[c] = 1
    elif fifths < 0:
        for c in ORDER_FLAT[:-fifths]:
            sig[c] = -1
    return sig


def spell(pitch: int, fifths: int, sig: dict[str, int]) -> tuple[str, int, int]:
    """MIDI pitch -> (letter, alteration, octave)."""
    pc = pitch % 12
    for letter, alt in sig.items():                # a diatonic note of the key
        if (LETTER_PC[letter] + alt) % 12 == pc:
            break
    else:
        letter, alt = (SHARPS[pc], SHARP_ALT[pc]) if fifths >= 0 else (FLATS[pc], FLAT_ALT[pc])
    octave = pitch // 12 - 1
    if letter == "c" and alt < 0 and pc == 11:
        octave += 1                                # C-flat belongs to the octave above B
    if letter == "b" and alt > 0 and pc == 0:
        octave -= 1                                # B-sharp to the octave below C
    return letter, alt, octave


ACCID = {1: "s", -1: "f", 0: "n", 2: "x", -2: "ff"}


class _BarState:
    """Accidentals already shown in this bar, per staff, letter and octave."""

    def __init__(self, sig: dict[str, int]) -> None:
        self.sig = sig
        self.shown: dict[tuple[str, int], int] = {}

    def attrs(self, letter: str, alt: int, octave: int) -> str:
        cur = self.shown.get((letter, octave), self.sig[letter])
        self.shown[(letter, octave)] = alt
        return f' accid="{ACCID[alt]}"' if alt != cur else (f' accid.ges="{ACCID[alt]}"' if alt else "")


def _note_xml(pitch: int, nid: str, ev_dur: str, dots: int, tie: str, fifths: int, state: _BarState,
              in_chord: bool) -> str:
    letter, alt, octave = spell(pitch, fifths, state.sig)
    acc = state.attrs(letter, alt, octave)
    dur = "" if in_chord else f' dur="{ev_dur}"' + (f' dots="{dots}"' if dots else "")
    t = f' tie="{tie}"' if tie else ""
    return f'<note xml:id="{nid}" pname="{letter}" oct="{octave}"{dur}{acc}{t}/>'


def _tie(tin: bool, tout: bool) -> str:
    return "m" if tin and tout else "i" if tout else "t" if tin else ""


def _layer(events: list[Event], fifths: int, sig: dict[str, int], pieces: dict[int, int], *, open_in: bool = True,
           open_out: bool = True) -> str:
    state = _BarState(sig)
    items: list[tuple[Event, str]] = []
    for ev in events:
        dur, dots = DUR[ev.dur]
        if not ev.pitches:
            items.append((ev, f'<rest dur="{dur}"' + (f' dots="{dots}"' if dots else "") + "/>"))
            continue
        notes = []
        for p, i, tin, tout in sorted(zip(ev.pitches, ev.ids, ev.tie_in, ev.tie_out)):
            # a tie can't reach outside this system's document: drop the ends that would
            tin = tin and (open_in or ev.start > 0 or pieces.get(i, 0) > 0)
            tout = tout and (open_out or ev.start + ev.dur < BAR)
            k = pieces.get(i, 0)
            pieces[i] = k + 1
            notes.append(_note_xml(p, f"n{i}-{k}", dur, dots, _tie(tin, tout), fifths, state, len(ev.pitches) > 1))
        if len(notes) == 1:
            items.append((ev, notes[0]))
        else:
            d = f' dur="{dur}"' + (f' dots="{dots}"' if dots else "")
            items.append((ev, f"<chord{d}>" + "".join(notes) + "</chord>"))
    # beam runs of eighths and shorter (notes or chords, not rests) within each beat
    out, run = [], []

    def flush():
        if len(run) >= 2:
            out.append("<beam>" + "".join(x for _, x in run) + "</beam>")
        else:
            out.extend(x for _, x in run)
        run.clear()

    for ev, xml in items:
        beamable = ev.pitches and ev.dur in (1, 2, 3)
        if beamable and run and run[-1][0].start // GRID != ev.start // GRID:
            flush()
        if beamable:
            run.append((ev, xml))
        else:
            flush()
            out.append(xml)
    flush()
    return "".join(out)


def system(bars: list[Bar], slots: int, fifths: int, *, title: str = "", meter: bool = True,
           pedal: bool = False) -> str:
    """One system: ``bars`` then invisible spaces up to ``slots`` bars.  ``meter``: show the time signature
    (the first system of a phrase); ``pedal``: Ped. / * marks (off: transcribed pedalling flickers)."""
    sig = signature(fifths)
    key = f"{fifths}s" if fifths > 0 else f"{-fifths}f" if fifths < 0 else "0"
    pieces: dict[int, int] = {}
    measures = []
    for j, b in enumerate(bars):
        lay = lambda n: _layer(b.staves[n], fifths, sig, pieces, open_in=j > 0, open_out=j < len(bars) - 1)  # noqa: E731
        staves = "".join(f'<staff n="{n + 1}"><layer n="1">{lay(n)}</layer></staff>' for n in (0, 1))
        ctrl = ""
        if b.dynamic:
            ctrl += f'<dynam staff="1" tstamp="1" place="below">{b.dynamic}</dynam>'
        for pos, what in (b.pedal if pedal else ()):
            ctrl += f'<pedal staff="2" tstamp="{1 + pos / GRID:.3f}" dir="{what}" place="below"/>'
        measures.append(f'<measure xml:id="m{j}" n="{b.index}">{staves}{ctrl}</measure>')
    n0 = bars[-1].index + 1 if bars else 1
    for j in range(len(bars), slots):
        measures.append(f'<measure xml:id="m{j}" n="{n0 + j - len(bars)}" right="invis">'
                        + "".join(f'<staff n="{n}"><layer n="1"><space dur="1"/></layer></staff>' for n in (1, 2))
                        + "</measure>")
    form = "" if meter else ' meter.form="invis"'
    head = (f'<scoreDef meter.count="4" meter.unit="4"{form} key.sig="{key}">'
            '<staffGrp symbol="brace" bar.thru="true">'
            '<staffDef n="1" lines="5" clef.shape="G" clef.line="2"/>'
            '<staffDef n="2" lines="5" clef.shape="F" clef.line="4"/>'
            "</staffGrp></scoreDef>")
    t = f"<title>{escape(title)}</title>" if title else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<mei xmlns="http://www.music-encoding.org/ns/mei" meiversion="4.0.1">'
            f"<meiHead><fileDesc><titleStmt>{t}</titleStmt><pubStmt/></fileDesc></meiHead>"
            f"<music><body><mdiv><score>{head}<section>{''.join(measures)}</section></score></mdiv></body></music></mei>")


assert BAR == 16


# -- per-bar engraving (live_score v2): every bar its own document at one staff size, never re-laid out ---------------
def _doc(score_def: str, measures: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<mei xmlns="http://www.music-encoding.org/ns/mei" meiversion="4.0.1">'
            "<meiHead><fileDesc><titleStmt><title/></titleStmt><pubStmt/></fileDesc></meiHead>"
            f"<music><body><mdiv><score>{score_def}<section>{measures}</section></score></mdiv></body></music></mei>")


def _keysig(fifths: int) -> str:
    return f"{fifths}s" if fifths > 0 else f"{-fifths}f" if fifths < 0 else "0"


def bar_doc(bar: Bar, fifths: int, *, pedal: bool = False) -> str:
    """One bar as it sits mid-line: no clefs, key or time signature, no brace (the line's header has them).
    Spelling and accidentals still follow the key; ties reaching other bars are dropped (separate documents)."""
    sig = signature(fifths)
    pieces: dict[int, int] = {}
    staves = "".join(f'<staff n="{n + 1}"><layer n="1">'
                     f"{_layer(bar.staves[n], fifths, sig, pieces, open_in=False, open_out=False)}</layer></staff>"
                     for n in (0, 1))
    ctrl = f'<dynam staff="1" tstamp="1" place="below">{bar.dynamic}</dynam>' if bar.dynamic else ""
    for pos, what in (bar.pedal if pedal else ()):
        ctrl += f'<pedal staff="2" tstamp="{1 + pos / GRID:.3f}" dir="{what}" place="below"/>'
    head = (f'<scoreDef meter.count="4" meter.unit="4" meter.form="invis" keysig="{_keysig(fifths)}" '
            'keysig.show="false"><staffGrp symbol="none" bar.thru="true">'
            '<staffDef n="1" lines="5" clef.shape="G" clef.line="2" clef.visible="false"/>'
            '<staffDef n="2" lines="5" clef.shape="F" clef.line="4" clef.visible="false"/>'
            "</staffGrp></scoreDef>")
    return _doc(head, f'<measure xml:id="m0" n="{bar.index}">{staves}{ctrl}</measure>')


def header_doc(fifths: int, *, meter: bool) -> str:
    """A line's start: brace, clefs, key signature (and the time signature when a phrase starts), no music."""
    form = "" if meter else ' meter.form="invis"'
    head = (f'<scoreDef meter.count="4" meter.unit="4"{form} keysig="{_keysig(fifths)}">'
            '<staffGrp symbol="brace" bar.thru="true">'
            '<staffDef n="1" lines="5" clef.shape="G" clef.line="2"/>'
            '<staffDef n="2" lines="5" clef.shape="F" clef.line="4"/>'
            "</staffGrp></scoreDef>")
    body = ('<measure xml:id="m0" n="1" right="invis">'
            + "".join(f'<staff n="{n}"><layer n="1"><space dur="32"/></layer></staff>' for n in (1, 2)) + "</measure>")
    return _doc(head, body)
