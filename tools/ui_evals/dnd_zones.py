"""Drag-and-drop eval: is the drop target the zone under the pointer? (owner, 19 Nov: "I have to point some way
vertically above the main area for it to show up as a drop zone").

Drives the real page headless: picks up a picture in a screen's bucket, moves over every drop zone on the page (tabs,
the prompt box, the strip's picture, sections, pictures) at its centre and just inside each edge, and compares the
page's target (window.__dnd, from src/dnd/DndProbe.tsx) with the zone drawn under the pointer. Then it cancels the
drag with Escape and lets go where it started, so nothing is ever dropped: no copy, no publish, no favourite.

    ~/.venvs/sm-room/bin/python tools/ui_evals/dnd_zones.py                 # desktop, top of the page
    ~/.venvs/sm-room/bin/python tools/ui_evals/dnd_zones.py --scroll 700    # scrolled: tab bar and strip pinned
    ~/.venvs/sm-room/bin/python tools/ui_evals/dnd_zones.py --phone --engine webkit
    ~/.venvs/sm-room/bin/python tools/ui_evals/dnd_zones.py --old           # the old detection, for comparison

Exit 1 if any point's target is wrong, or the page scrolled away from a tab or the strip's picture while the
pointer was on it. Sortable favourites are matched on their places before the drag (pickDrop.ts),
so points over them are listed but not judged.
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

# in the page: the zone drawn under (x, y), past the dragged card, and the page's own target
PROBE_JS = """([x, y]) => {
  const d = window.__dnd;
  if (!d) return null;
  const zones = d.zones();
  const self = zones.find(z => z.id === d.active);
  const dragged = document.querySelector('[aria-roledescription="draggable"][aria-pressed="true"]') || (self && self.node);
  let top = null;
  for (const el of document.elementsFromPoint(x, y)) {
    if ((dragged && dragged.contains(el)) || el.closest('.dnd-drag-overlay')) continue;  // the card and its overlay
    top = el; break;
  }
  const holding = zones.filter(z => z.id !== d.active && z.node && top && z.node.contains(top));
  holding.sort((a, b) => a.node.contains(b.node) ? 1 : b.node.contains(a.node) ? -1 : 0);
  const want = holding[0] || null;
  // the target is a sortable favourite inside the zone drawn here: the favourites slide aside while dragging, so it
  // is matched on its place before the drag (pickDrop.ts), not on what is drawn
  const tz = zones.find(z => z.id === d.over);
  const slot = !!(tz && tz.sortable && want && want.node.contains(tz.node));
  return { want: want ? String(want.id) : null, sortable: !!(want && want.sortable) || slot, over: d.over == null ? null : String(d.over) };
}"""

# in the page: points to try, at each zone's centre and 4 px inside each edge, within the viewport
POINTS_JS = """([maxPictures]) => {
  const d = window.__dnd;
  const vw = innerWidth, vh = innerHeight, out = [];
  let pictures = 0;
  for (const z of d.zones()) {
    if (z.id === d.active || !z.node) continue;
    const id = String(z.id);
    const isZone = id.startsWith('drop-');
    if (!isZone && ++pictures > maxPictures) continue;
    const r = z.node.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (const [where, x, y] of [['centre', cx, cy], ['top', cx, r.top + 4], ['bottom', cx, r.bottom - 4],
                                 ['left', r.left + 4, cy], ['right', r.right - 4, cy]]) {
      if (x > 2 && y > 2 && x < vw - 2 && y < vh - 2) out.push({ zone: id, where, x: Math.round(x), y: Math.round(y) });
    }
  }
  return out;
}"""


def run(url: str, tab: str, width: int, height: int, scroll: int, engine: str, pictures: int, old: bool) -> int:
    with sync_playwright() as pw:
        browser = getattr(pw, engine).launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        if old:
            page.add_init_script("window.__dndOld = true")
        page.goto(url)
        page.get_by_text(tab, exact=True).first.click(timeout=20000)
        page.wait_for_selector("[data-section-variant] .drag-grid > div img", timeout=30000)
        page.wait_for_timeout(1500)
        if scroll:
            page.mouse.move(width / 2, height * 0.6)
            page.mouse.wheel(0, scroll)
            page.wait_for_timeout(800)

        # a dated picture in view if there is one (plain draggable), else any picture in view
        card = None
        for sel in ['[data-section-variant="dated"] .drag-grid > div', "[data-section-variant] .drag-grid > div"]:
            cards = page.locator(sel)
            for i in range(min(cards.count(), 60)):
                box = cards.nth(i).bounding_box()
                if box and 60 < box["y"] + box["height"] / 2 < height - 60:
                    card = box
                    break
            if card:
                break
        if not card:
            print("no picture in view to pick up")
            return 2
        sx, sy = card["x"] + card["width"] / 2, card["y"] + card["height"] / 2

        page.mouse.move(sx, sy)
        page.mouse.down()
        page.mouse.move(sx + 12, sy + 12, steps=4)
        page.wait_for_function("window.__dnd && window.__dnd.active != null", timeout=5000)

        points = page.evaluate(POINTS_JS, [pictures])
        # the tabs and the strip's picture first: hovering a picture near the top autoscrolls the page (as it should),
        # which unpins the bars before their turn
        points.sort(key=lambda p: not (p["zone"].startswith("drop-tab-") or p["zone"] == "drop-published"))
        rows, wrong = [], 0
        try:
            for p in points:
                page.mouse.move(p["x"], p["y"], steps=3)
                page.wait_for_timeout(120)
                a = page.evaluate(PROBE_JS, [p["x"], p["y"]])
                page.wait_for_timeout(80)
                b = page.evaluate(PROBE_JS, [p["x"], p["y"]])  # settled: autoscroll may still be moving the page
                res = b or a
                if res is None:
                    print("window.__dnd missing: is DndProbe in this build?")
                    return 2
                judged = not res["sortable"]
                ok = res["over"] == res["want"] or not judged
                # the centre of a tab or the strip's picture must still be under the pointer: the page must not have
                # scrolled away (autoscroll) and unpinned the bar
                if p["where"] == "centre" and (p["zone"].startswith("drop-tab-") or p["zone"] == "drop-published"):
                    judged, ok = True, ok and res["want"] == p["zone"]
                if judged and not ok:
                    wrong += 1
                rows.append((p, res, ok, judged))
        finally:
            page.keyboard.press("Escape")  # cancels the drag: the release below drops nothing
            page.mouse.move(sx, sy, steps=3)
            page.mouse.up()
            browser.close()

    print(f"{engine} {width}x{height} scroll={scroll} tab={tab!r}{' OLD detection' if old else ''}: {len(rows)} points")
    for p, res, ok, judged in rows:
        mark = "ok " if ok else "BAD"
        if not judged:
            mark = "-- "
        print(f"  {mark} {p['zone'][:34]:34} {p['where']:6} ({p['x']:4},{p['y']:4})  "
              f"under={str(res['want'])[:34]:34} target={str(res['over'])[:34]}")
    judged = sum(1 for r in rows if r[3])
    print(f"wrong: {wrong} of {judged} judged points")
    return 1 if wrong else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:5000/", help="this machine passes the sign-in gates (docs/ACCOUNTS.md)")
    ap.add_argument("--tab", default="North screen", help="the bucket tab to drag from")
    ap.add_argument("--scroll", type=int, default=0, help="scroll the page this far first (pins the tab bar)")
    ap.add_argument("--phone", action="store_true", help="390x844 instead of 1400x1000")
    ap.add_argument("--engine", default="chromium", choices=["chromium", "webkit", "firefox"])
    ap.add_argument("--pictures", type=int, default=6, help="how many pictures to try as targets")
    ap.add_argument("--old", action="store_true", help="the old detection (closestCenter), for comparison")
    a = ap.parse_args()
    w, h = (390, 844) if a.phone else (1400, 1000)
    sys.exit(run(a.url, a.tab, w, h, a.scroll, a.engine, a.pictures, a.old))


if __name__ == "__main__":
    main()
