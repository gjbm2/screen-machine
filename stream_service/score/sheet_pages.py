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
