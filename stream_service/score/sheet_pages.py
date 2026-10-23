"""A whole piece's score (sheet.py's MEI) laid out by Verovio: pages for a screen, or a PDF to print (owner, 23 Oct:
"output a score to pdf (formatted for A4 portrait by default) so user can print").

Kept apart from sheet.py so a change here doesn't count as a new generator (sheet.generator_version: cached scores
are only written again when the notes would come out differently).  The PDF is printed by headless Chromium
(Playwright 1.58 in sm-stream's venv, the browser already in ~/.cache/ms-playwright, as room_service's snapshotter
uses): it draws Verovio's SVG exactly as the page does, vector throughout (cairosvg took 5 s a page on a busy box,
MuPDF misread Verovio's nested viewBox).
"""
from __future__ import annotations

import html
import re

STAFF_UNITS = 72.0                   # a staff's height in Verovio units at scale 100 (4 spaces of 2 x unit 9)
PAPERS = {"a4": (210.0, 297.0), "a4-landscape": (297.0, 210.0), "letter": (215.9, 279.4)}    # mm
PRINT_STAFF_MM = 7.0                 # a printed piano score's staff (rastral 2-3: 7-7.5 mm)
PRINT_MARGINS_MM = (14.0, 12.0, 12.0, 12.0)    # top, right, bottom, left (inside a printer's unprintable edge)


def _toolkit(mei: str, page_w: float, page_h: float, margins: tuple[float, float, float, float], header: bool = True):
    """A Verovio toolkit with ``mei`` laid out on pages page_w x page_h (Verovio units at scale 100); ``header``: the
    title and composer on the first page and page numbers on the rest (paper; a screen's page shows its own)."""
    import verovio
    top, right, bottom, left = (int(m) for m in margins)
    tk = verovio.toolkit()
    tk.setOptions({"scale": 100, "pageWidth": int(max(400, page_w)), "pageHeight": int(max(400, page_h)),
                   "pageMarginTop": top, "pageMarginRight": right, "pageMarginBottom": bottom, "pageMarginLeft": left,
                   "adjustPageHeight": False, "breaks": "smart", "breaksSmartSb": 0.0, "header": "auto" if header else "none",
                   "footer": "none", "svgViewBox": True, "font": "Leipzig", "justifyVertically": True,
                   "pedalStyle": "line"})
    if not tk.loadData(mei):
        raise ValueError("Verovio couldn't read the score")
    return tk


def pages(mei: str, width_px: float, height_px: float, staff_px: float = 36.0) -> list[str]:
    """The score laid out as pages ``width_px`` x ``height_px`` (css px) with a staff ``staff_px`` high: SVGs that
    scale to that box (viewBox)."""
    k = STAFF_UNITS / max(12.0, staff_px)                  # Verovio units (scale 100) per css px
    tk = _toolkit(mei, width_px * k, height_px * k, (50, 50, 50, 50), header=False)    # the page names the piece
    return [tk.renderToSVG(p) for p in range(1, tk.getPageCount() + 1)]


def pdf(mei: str, paper: str = "a4", staff_mm: float = PRINT_STAFF_MM, title: str = "", composer: str = "") -> bytes:
    """The score as a PDF on ``paper`` (PAPERS: a4 portrait by default), a staff ``staff_mm`` high, one page per
    sheet.  ``title`` and ``composer`` head the first page (they replace the MEI's: the piece may have been renamed
    since it was written) and name the document."""
    from playwright.sync_api import sync_playwright
    if title or composer:                                 # the piece's names now (renamed since it was written)
        from xml.sax.saxutils import escape
        names = (f"<title>{escape(title)}</title>" if title else "<title/>") + \
                (f"<composer>{escape(composer)}</composer>" if composer else "")
        mei = re.sub(r"<titleStmt>.*?</titleStmt>", lambda _m: f"<titleStmt>{names}</titleStmt>", mei, count=1,
                     flags=re.S)
    w_mm, h_mm = PAPERS[paper]
    k = STAFF_UNITS / max(3.0, staff_mm)                   # Verovio units per mm
    tk = _toolkit(mei, w_mm * k, h_mm * k, tuple(m * k for m in PRINT_MARGINS_MM))
    sheets = "".join(f'<div class="p">{tk.renderToSVG(n)}</div>' for n in range(1, tk.getPageCount() + 1))
    doc = ("<!doctype html><html><head><meta charset='utf-8'>"
           f"<title>{html.escape(title or 'Score')}</title><style>"
           f"@page {{ size: {w_mm:g}mm {h_mm:g}mm; margin: 0 }} html, body {{ margin: 0; background: #fff }}"
           f" .p {{ width: {w_mm:g}mm; height: {h_mm:g}mm; overflow: hidden; break-after: page }}"
           " .p:last-child { break-after: auto } .p > svg { display: block; width: 100%; height: 100% }"
           f"</style></head><body>{sheets}</body></html>")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(doc, wait_until="load")
            return page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            browser.close()


# -- a long score in page-sized pieces --------------------------------------------------------------------------------
# Verovio engraves at about 0.5-0.7 ms a note on this box (measured on Islamey, 23 Oct: 24 bars of 400-800 notes
# 0.2-0.5 s; all 331 bars, 9,500 notes, 6-10 s, half of it casting the music off into systems and pages; its options
# make no difference). A whole score laid out before its first page shows makes a long piece wait for all of it (owner:
# "WHAT THE FUCK IS THE MACHINE DOING THAT TAKES 5-7 SECONDS"). Laid out a page or two at a time instead, the first
# pages are there in a few tenths of a second and the rest follow behind (sheet_worker draws them while idle).
CHUNK_BARS = 16                      # the first piece's bars (its pages at once) ...
CHUNK_PAGES = 5.5                    # ... later ones this many pages' worth (the page each piece re-lays is less waste)

_ITEM = re.compile(r"<measure .*?</measure>|<scoreDef[^>]*/>|<sb/>", re.S)
_KEYSIG = re.compile(r'keysig="([^"]*)"')
_METER = re.compile(r'meter.count="(\d+)" meter.unit="(\d+)"')
_MNUM = re.compile(r'xml:id="m(\d+)"')


class Paginator:
    """The score laid out in pieces, as far as asked: ``pages`` [(toolkit, its page, first bar, last bar)] so far,
    ``done`` once the last is laid out, ``more()`` lays out the next piece, ``estimate`` the page count expected.
    Each piece starts on a fresh page with the key and time in force there (the time signature not shown again); all
    of a piece's pages are kept but the last, which the next piece starts from (no page left half full), except at
    the end. A tie or an octave line across a piece's edge is dropped (Verovio's warning only)."""

    def __init__(self, mei: str, page_w: float, page_h: float, margins: tuple, header: bool = False) -> None:
        self.args = (page_w, page_h, margins)
        self.header = header
        self.pages: list = []
        self.done = False
        a, b = mei.find("<section>"), mei.rfind("</section>")
        if a < 0 or b < 0:
            tk = _toolkit(mei, page_w, page_h, margins, header)
            self.pages = [(tk, p, 0, 0) for p in range(1, tk.getPageCount() + 1)]
            self.done, self.items = True, []
            return
        self.head, self.tail = mei[:a + 9], mei[b:]
        self.items = _ITEM.findall(mei[a + 9:b])
        self.meas = [i for i, x in enumerate(self.items) if x.startswith("<measure")]
        self.nums = [int(_MNUM.search(self.items[i]).group(1)) for i in self.meas]
        # the first piece about two pages' worth: more bars fit a page the smaller the staff (page_w is in staff
        # heights x STAFF_UNITS), so a small staff doesn't lay out a piece too short for a page, then double it
        area = (page_w / STAFF_UNITS) * (page_h / STAFF_UNITS) / ((1180 / 40) * (780 / 40))
        self.pos, self.n_bars = 0, int(min(96, max(8, CHUNK_BARS * area)))

    @property
    def estimate(self) -> int:
        if self.done or not self.pages:
            return len(self.pages)
        per = max(1e-6, self.pos / len(self.pages))                  # bars a page so far
        return len(self.pages) + max(1, round((len(self.meas) - self.pos) / per))

    def more(self) -> None:
        """Lay out the next piece (a no-op once done)."""
        while not self.done:
            items, meas, nums, pos = self.items, self.meas, self.nums, self.pos
            lo = meas[pos]
            while lo > 0 and not items[lo - 1].startswith("<measure"):
                lo -= 1                                   # a key or time change just before the piece: in it
            key = meter = None
            for x in items[:lo]:                          # what's in force where the piece starts
                if x.startswith("<scoreDef"):
                    k, m = _KEYSIG.search(x), _METER.search(x)
                    key = k.group(1) if k else key
                    meter = m.groups() if m else meter
            h = self.head
            if key is not None:
                h = _KEYSIG.sub(f'keysig="{key}"', h, count=1)
            if pos > 0:                                   # carrying on: the time in force, not printed again
                h = _METER.sub(lambda m: f'meter.count="{(meter or m.groups())[0]}" '
                               f'meter.unit="{(meter or m.groups())[1]}" meter.form="invis"', h, count=1)
            end = pos + self.n_bars >= len(meas)
            hi = len(items) if end else meas[pos + self.n_bars]
            tk = _toolkit(h + "".join(items[lo:hi]) + self.tail, *self.args, self.header and pos == 0)
            count = tk.getPageCount()
            on: dict[int, list] = {}
            for nb in (nums[pos:] if end else nums[pos:pos + self.n_bars]):
                try:
                    pg = int(tk.getPageWithElement(f"m{nb}"))
                except Exception:                         # noqa: BLE001
                    continue
                on.setdefault(pg, []).append(nb)
            keep = count if end else count - 1
            if keep < 1:                                  # one page didn't fill: a longer piece
                self.n_bars *= 2
                continue
            for p in range(1, keep + 1):
                bars = on.get(p) or [0]
                self.pages.append((tk, p, min(bars), max(bars)))
            if end:
                self.done = True
                return
            new_pos = nums.index(min(on.get(count) or [nums[pos + self.n_bars]]))
            if new_pos <= pos:                            # never stand still
                new_pos = pos + max(1, self.n_bars // 2)
            self.n_bars = max(8, int((new_pos - pos) / keep * CHUNK_PAGES) + 1)
            self.pos = new_pos
            return

    def upto(self, n: int) -> None:
        """Lay out until page n exists (or the end)."""
        while not self.done and len(self.pages) < n:
            self.more()
