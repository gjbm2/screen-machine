"""The engraving worker: MEI in, ink out, in its own process (Verovio and resvg hold the GIL for ~0.1-0.8 s, which
the render thread must never wait on).

    python -m stream_service.score.engrave        (stdin / stdout pipes; started by live_score.py's Engraver)

Protocol, both ways: a 4-byte big-endian length, then that many bytes of JSON.  A reply's JSON is followed by a
second length-prefixed blob: the ink coverage, w * h bytes (0 = paper, 255 = ink).
  request  {"id", "mei", "width": px, "scale": verovio scale}
           (or "space_px": a fixed staff space, plus "margin_left", and "fit_px" / "squeeze": see engrave())
           optional, each off unless asked for:
             "spacing": "proportional"  Verovio spaces notes linearly in time (spacingNonLinear 1.0, spacingLinear
                                        0.05), so a time-proportional provisional x lands nearer its engraved head;
                                        "classic" (or absent) is Verovio's default, as before
             "notes": true              the reply carries "notes" (below)
             "ink": "no_notes"          the bitmap leaves out noteheads, stems (not their flags), accidentals and
                                        ledger lines: the scene draws its notes, the ink adds beams, flags, rests,
                                        dots, ties, slurs, dynamics, octave lines and bar lines
  reply    {"id", "ok", "w", "h", "ms", "measures": [{"id", "x0", "x1", "staves": [[top, bottom], [top, bottom]]}],
            "error"?}   (with fit_px also "natural", "fit" and "heads": [[note id, cx, cy], ...])
           with "notes": also "notes": [{"id", "x", "y", "glyph", "stem", "dir", "accid"}, ...] per note (its first
             piece): the head's centre (its glyph's box) and SMuFL glyph name (noteheadBlack / Half / Whole), the
             stem [x, top, bottom] (the chord's, shared, for a note in a chord) and "up" / "down", or None and None
             (a whole note), and the accidental shown {"glyph": e.g. "accidentalSharp", "x", "y": its box's centre}
             or None; a note tied on within the bar also has "tied": its later pieces, each {"x", ..., "accid"}
             likewise (with "ink": "no_notes" their heads are left out of the ink too)
Geometry is in the bitmap's pixels: each measure's x range and its staves' top and bottom line heights, so
the scene can place a note at a pitch before it is engraved, and with fit_px each note's head centre (its first
piece, ``n<id>-0``), so a glowing note can sink into its own ink.  Verovio's SVG nests a definition-scale viewBox
and a page-margin translate inside the page; both are applied here.

The ink is drawn white on an opaque black page and read from the red channel: the same bytes as the alpha of a
transparent page (source-over is the same sum on every channel), but resvg's PNG of an opaque page encodes faster.
"""
from __future__ import annotations

import io
import json
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET

OPTIONS = {"adjustPageHeight": True, "breaks": "none", "header": "none", "footer": "none", "svgViewBox": True,
           "pageMarginLeft": 60, "pageMarginRight": 60, "pageMarginTop": 40, "pageMarginBottom": 40,
           "font": "Leipzig"}
SPACING = {"classic": {"spacingLinear": 0.25, "spacingNonLinear": 0.6},      # Verovio's defaults (options stick
           "proportional": {"spacingLinear": 0.05, "spacingNonLinear": 1.0}}  # in the toolkit: always set one)
_PATH = re.compile(r"M\s*([-\d.]+)[ ,]([-\d.]+)\s*L\s*([-\d.]+)[ ,]([-\d.]+)")
_TRANSLATE = re.compile(r"translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)")
SVG = "{http://www.w3.org/2000/svg}"


def _geometry(svg: str, width_px: float):
    """(measures, the definition-scale svg element, k, ox, oy): px = (svg units + offset) * k."""
    root = ET.fromstring(svg)
    inner = next(e for e in root.iter(f"{SVG}svg") if "definition-scale" in (e.get("class") or ""))
    vw = float(inner.get("viewBox").split()[2])
    k = width_px / vw
    ox = oy = 0.0
    for g in inner.iter(f"{SVG}g"):
        if "page-margin" in (g.get("class") or ""):
            m = _TRANSLATE.search(g.get("transform") or "")
            if m:
                ox, oy = float(m.group(1)), float(m.group(2))
            break
    out = []
    for g in inner.iter(f"{SVG}g"):
        if "measure" not in (g.get("class") or "").split():
            continue
        staves, xs = [], []
        for st in g:
            if "staff" not in (st.get("class") or "").split():
                continue
            ys = []
            for p in st:
                if p.tag != f"{SVG}path":
                    continue
                m = _PATH.match(p.get("d") or "")
                if m and abs(float(m.group(2)) - float(m.group(4))) < 1e-3:
                    ys.append((float(m.group(2)) + oy) * k)
                    xs += [(float(m.group(1)) + ox) * k, (float(m.group(3)) + ox) * k]
            if len(ys) >= 2:
                staves.append([min(ys), max(ys)])            # top and bottom line
        if staves and xs:
            out.append({"id": g.get("id"), "x0": round(min(xs), 1), "x1": round(max(xs), 1),
                        "staves": [[round(y, 1) for y in s] for s in staves]})
    return out, inner, k, ox, oy


def geometry(svg: str, width_px: float) -> list[dict]:
    return _geometry(svg, width_px)[0]


_NOTE_ID = re.compile(r"n(\d+)-0")


PIECE = 1_000_000                  # heads(pieces=True): piece k of note i (a tie's later heads) is i + k * PIECE
_PIECE_ID = re.compile(r"n(\d+)-(\d+)")


def heads(inner, k: float, ox: float, oy: float, space_px: float, pieces: bool = False) -> list[list]:
    """[note id, cx, cy] (bitmap px) of each note's first piece: its notehead glyph's origin, half a head right.
    ``pieces``: every piece of a tied note too, a later one as id + piece * PIECE (the score's light, which reaches
    the held part of a tie when the music gets there)."""
    out = []
    for g in inner.iter(f"{SVG}g"):
        if "note" not in (g.get("class") or "").split():
            continue
        m = (_PIECE_ID if pieces else _NOTE_ID).fullmatch(g.get("id") or "")
        use = next(g.iter(f"{SVG}use"), None) if m else None
        if use is None:
            continue
        if use.get("x") is not None:
            x, y = float(use.get("x")), float(use.get("y") or 0.0)
        else:
            t = _TRANSLATE.search(use.get("transform") or "")
            if not t:
                continue
            x, y = float(t.group(1)), float(t.group(2))
        nid = int(m.group(1)) + (int(m.group(2)) * PIECE if pieces else 0)
        out.append([nid, round((x + ox) * k + 0.59 * space_px, 1), round((y + oy) * k, 1)])
    return out


_GLYPHS: dict = {}                 # SMuFL code -> (name, x, y, w, h): the font's glyph boxes (font units, y up)
_HREF = re.compile(r"#([0-9A-F]{4})-")
_SCALE = re.compile(r"scale\(\s*([-\d.]+)")
XLINK = "{http://www.w3.org/1999/xlink}href"


def _glyph_table() -> dict:
    if not _GLYPHS:
        import pathlib

        import verovio
        path = pathlib.Path(verovio.__file__).with_name("data") / f"{OPTIONS['font']}.xml"
        for g in ET.parse(path).getroot().iter("g"):
            if g.get("c") and g.get("n"):
                _GLYPHS[g.get("c")] = (g.get("n"), *(float(g.get(a, 0.0)) for a in ("x", "y", "w", "h")))
    return _GLYPHS


_ROOT_BOX = re.compile(r'<svg\b[^>]*?viewBox="([^"]+)"')


def _frame(svg: str, inner, ox: float, oy: float, zoom: float) -> tuple:
    """(k, tx, ty): bitmap px = svg units (inside the page margin) * k + (tx, ty), exactly as resvg draws them.  The
    definition-scale viewBox sits in the page's (rounded) viewBox at the smaller of the two scales, centred
    (preserveAspectRatio's default), so its x scale is not quite the bitmap's width over its width (``_geometry``'s
    k, up to ~1.5 px off across a bar)."""
    rw, rh = (float(v) for v in _ROOT_BOX.search(svg).group(1).split()[2:4])
    vx, vy, vw, vh = (float(v) for v in inner.get("viewBox").split())
    s = min(rw / vw, rh / vh)
    k = s * zoom
    return k, ((rw - vw * s) / 2 - vx * s) * zoom + ox * k, ((rh - vh * s) / 2 - vy * s) * zoom + oy * k


def _glyph_at(use, f: tuple) -> tuple | None:
    """A placed glyph (a <use>): (SMuFL name, its box's centre x, y in bitmap px); ``f``: _frame's."""
    m = _HREF.search(use.get(XLINK) or use.get("href") or "")
    t = _TRANSLATE.search(use.get("transform") or "")
    g = _glyph_table().get(m.group(1)) if m else None
    if g is None or t is None:
        return None
    s = _SCALE.search(use.get("transform") or "")
    s = float(s.group(1)) if s else 1.0
    name, gx, gy, gw, gh = g
    x = float(t.group(1)) + s * (gx + 0.5 * gw)
    y = float(t.group(2)) - s * (gy + 0.5 * gh)            # the glyph is drawn flipped (font y is up)
    return name, round(x * f[0] + f[1], 1), round(y * f[0] + f[2], 1)


def _stem_line(g):
    """A stem group's line (x, y0, y1) in svg units: its path (M x y L x y) or rect."""
    for p in g:
        if p.tag == f"{SVG}path":
            m = _PATH.match(p.get("d") or "")
            if m:
                return float(m.group(1)), float(m.group(2)), float(m.group(4))
        elif p.tag == f"{SVG}rect":
            x, y = float(p.get("x", 0)), float(p.get("y", 0))
            return x + 0.5 * float(p.get("width", 0)), y, y + float(p.get("height", 0))
    return None


_PIECE = re.compile(r"n(\d+)-(\d+)")


def notes(inner, f: tuple) -> list[dict]:
    """Each note's first piece (``n<id>-0``) as engraved: head centre and glyph, stem and its direction (a chord's
    stem for each of its notes), the accidental shown; its later (tied) pieces in "tied", in order.  See the module
    docstring; ``f``: _frame's."""
    out: list[dict] = []
    later: dict = {}                                        # note id -> [(piece, entry)]
    spans: dict = {}                                        # stem owner -> its heads' y range (px)

    def walk(g, stem):
        for c in g:
            if c.tag != f"{SVG}g":
                continue
            cls = (c.get("class") or "").split()
            if "chord" in cls:
                st = next((s for s in c if "stem" in (s.get("class") or "").split()), None)
                walk(c, (st, c))
            elif "note" in cls:
                m = _PIECE.fullmatch(c.get("id") or "")
                if m:
                    note(c, int(m.group(1)), int(m.group(2)), stem)
            else:
                walk(c, stem)

    def note(g, nid, piece, stem):
        head = acc = None
        own = None
        for c in g:
            cls = (c.get("class") or "").split()
            if "notehead" in cls:
                use = next(c.iter(f"{SVG}use"), None)
                head = _glyph_at(use, f) if use is not None else None
            elif "accid" in cls:
                use = next(c.iter(f"{SVG}use"), None)
                acc = _glyph_at(use, f) if use is not None else None
            elif "stem" in cls:
                own = c
        if head is None:
            return
        st, owner = (own, g) if own is not None else (stem if stem is not None else (None, None))
        line = _stem_line(st) if st is not None else None
        stem_px = d = None
        if line is not None:
            sx, y0, y1 = line
            top, bot = (round(min(y0, y1) * f[0] + f[2], 1), round(max(y0, y1) * f[0] + f[2], 1))
            if id(owner) not in spans:                      # the heads the stem carries: it reaches past them
                ys = [h[2] for hg in owner.iter(f"{SVG}g") if "notehead" in (hg.get("class") or "").split()
                      for u in hg.iter(f"{SVG}use") for h in [_glyph_at(u, f)] if h is not None]
                spans[id(owner)] = (min(ys), max(ys)) if ys else (head[2], head[2])
            lo, hi = spans[id(owner)]
            d = "up" if lo - top > bot - hi else "down"
            stem_px = [round(sx * f[0] + f[1], 1), top, bot]
        e = {"x": head[1], "y": head[2], "glyph": head[0], "stem": stem_px, "dir": d,
             "accid": {"glyph": acc[0], "x": acc[1], "y": acc[2]} if acc else None}
        if piece == 0:
            out.append({"id": nid, **e})
        else:
            later.setdefault(nid, []).append((piece, e))

    walk(inner, None)
    for e in out:
        if e["id"] in later:
            e["tied"] = [x for _k, x in sorted(later[e["id"]], key=lambda t: t[0])]
    return out


_DROP = ("notehead", "accid", "ledgerLines")


def strip_notes(svg: str) -> str:
    """The SVG without noteheads, stem lines (their flags stay), accidentals and ledger lines."""
    ET.register_namespace("", SVG[1:-1])
    ET.register_namespace("xlink", XLINK[1:XLINK.index("}")])
    root = ET.fromstring(svg)
    gone = []
    for parent in root.iter():
        stem = "stem" in (parent.get("class") or "").split()
        for c in parent:
            cls = (c.get("class") or "").split()
            if (c.tag == f"{SVG}g" and cls and cls[0] in _DROP) or (stem and c.tag in (f"{SVG}path", f"{SVG}rect")):
                gone.append((parent, c))
    for parent, c in gone:
        parent.remove(c)
    return ET.tostring(root, encoding="unicode")


def _raster(svg: str, zoom: float | None, width: int):
    """The ink coverage (an L image) of the SVG at ``zoom`` (or scaled to ``width`` px).  Drawn white on an opaque
    black page and read from the red channel (see the module docstring); the transparent render if the SVG isn't
    Verovio's usual shape."""
    import resvg_py
    from PIL import Image
    size = {"zoom": zoom} if zoom is not None else {"width": width}
    white, n = re.subn(r'(<svg class="definition-scale"[^>]*?) color="black"', r'\1 color="white" fill="white"', svg,
                       count=1)
    if n:
        png = bytes(resvg_py.svg_to_bytes(svg_string=white, background="black", skip_system_fonts=True, **size))
        return Image.open(io.BytesIO(png)).getchannel("R")
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True, **size))
    im = Image.open(io.BytesIO(png))
    return im.getchannel("A") if "A" in im.getbands() else im.convert("L").point(lambda v: 255 - v)


_CAL: dict = {}                    # verovio scale -> staff space (px) at zoom 1


def _staff_space(tk, scale: int) -> float:
    """One staff space in raster px at resvg zoom 1 for this Verovio scale (measured once on a tiny bar)."""
    if scale not in _CAL:
        from .mei import bar_doc
        from .quantize import Bar, Event
        tk.setOptions({**OPTIONS, **BAR_OPTIONS, "scale": scale})
        tk.loadData(bar_doc(Bar(index=1, staves=([Event(0, 16)], [Event(0, 16)]), t_start=0, t_end=0), 0))
        svg = tk.renderToSVG(1)
        root_w = float(ET.fromstring(svg).get("viewBox").split()[2])     # resvg's width at zoom 1
        m = geometry(svg, root_w)
        top, bot = m[0]["staves"][0]
        _CAL[scale] = (bot - top) / 4.0
    return _CAL[scale]


BAR_OPTIONS = {"adjustPageWidth": True, "pageMarginLeft": 8, "pageMarginRight": 8}


def _placed_width(svg: str, zoom: float, space_px: float) -> float | None:
    """A bar's width as placed (its system-start line cropped), in output px, from the SVG alone."""
    root_w = float(ET.fromstring(svg).get("viewBox").split()[2])
    ms = geometry(svg, root_w * zoom)
    if not ms:
        return None
    return ms[0]["x1"] + 1 - (ms[0]["x0"] + 0.35 * space_px)


def engrave(tk, req: dict) -> tuple[dict, bytes]:
    """``width``: scale the page to that many px; ``space_px``: a fixed zoom giving staff spaces that size (one
    staff size for every bar), the page as wide as its music (``margin_left`` px of room before it, for a brace).
    With ``space_px``, ``fit_px`` justifies the bar to that placed width (never below ``squeeze`` of its natural
    width): a natural pass measures it, a justified pass lays it out to the target (corrected once if it lands
    more than half a staff space off), then one rasterisation.  With ``spacing`` "proportional" the justified pass
    starts from the exact page width (the natural pass's margins and crop), so it rarely needs the correction.
    ``notes`` and ``ink``: see the module docstring."""
    t0 = time.perf_counter()
    scale = int(req.get("scale", 50))
    fit = req.get("fit_px")
    prop = req.get("spacing") == "proportional"
    spacing = SPACING["proportional" if prop else "classic"]
    extra: dict = {}
    if req.get("space_px"):
        sp = float(req["space_px"])
        zoom = sp / _staff_space(tk, scale)
        ml = int(req.get("margin_left", 8))
        base = {**OPTIONS, **BAR_OPTIONS, **spacing, "scale": scale, "pageMarginLeft": ml}
        tk.setOptions(base)
    else:
        zoom = None
        tk.setOptions({**OPTIONS, **spacing, "pageWidth": int(req.get("page_width", 3800)), "scale": scale})
    if not tk.loadData(req["mei"]):
        return {"id": req.get("id"), "ok": False, "error": tk.getLog()[-500:]}, b""
    svg = tk.renderToSVG(1)
    if zoom is not None and fit:
        natural = _placed_width(svg, zoom, sp)
        if natural is not None:
            target = max(float(fit), float(req.get("squeeze", 0.85)) * natural)
            u = scale / 100 * zoom if prop else 0.5 * zoom          # output px per unit of page width
            if prop:      # placed = (page - margins) * u + 1 - the 0.35 sp crop
                pw = (target - 1 + 0.35 * sp) / u + ml + BAR_OPTIONS["pageMarginRight"]
            else:
                pw = (target + 16) / u
            for _ in range(2):
                tk.setOptions({**base, "adjustPageWidth": False, "breaks": "auto", "minLastJustification": 0,
                               "pageWidth": int(pw)})
                tk.loadData(req["mei"])
                got_svg = tk.renderToSVG(1)
                got = _placed_width(got_svg, zoom, sp)
                if got is None:
                    break
                svg = got_svg
                if abs(got - target) <= 0.5 * sp:
                    break
                pw += (target - got) / u
            extra = {"natural": round(natural, 1), "fit": round(target, 1)}
    ink = _raster(strip_notes(svg) if req.get("ink") == "no_notes" else svg, zoom,
                  int(req["width"]) if zoom is None else 0)
    measures, inner, k, ox, oy = _geometry(svg, ink.width)
    if extra:
        extra["heads"] = heads(inner, k, ox, oy, sp)
    if req.get("notes"):
        rw = float(_ROOT_BOX.search(svg).group(1).split()[2])
        extra["notes"] = notes(inner, _frame(svg, inner, ox, oy, zoom if zoom is not None else ink.width / rw))
    return ({"id": req.get("id"), "ok": True, "w": ink.width, "h": ink.height, "measures": measures,
             "ms": round(1000 * (time.perf_counter() - t0)), **extra}, ink.tobytes())


def _read(f) -> bytes | None:
    head = f.read(4)
    if len(head) < 4:
        return None
    n, = struct.unpack(">I", head)
    return f.read(n)


def _write(f, data: bytes) -> None:
    f.write(struct.pack(">I", len(data)))
    f.write(data)


def main() -> None:
    import verovio
    verovio.enableLog(verovio.LOG_ERROR)
    tk = verovio.toolkit()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        raw = _read(stdin)
        if raw is None:
            return
        try:
            meta, ink = engrave(tk, json.loads(raw))
        except Exception as e:  # report, keep serving
            meta, ink = {"ok": False, "error": f"{type(e).__name__}: {e}"}, b""
        _write(stdout, json.dumps(meta).encode())
        _write(stdout, ink)
        stdout.flush()


if __name__ == "__main__":
    main()
