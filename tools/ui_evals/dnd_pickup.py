"""Standing drag-and-drop check (owner, 24 Nov: "dnd is fragile ... we need to catch regressions"): can a picture be
picked up, and carried onto a screen's tab, on a phone and on a desktop?

Drives the real page headless. It picks a screen's bucket that has Favourites, then, for a favourite and for a picture
in a date section, holds it, carries it to another screen's tab and checks the page's drag (window.__dnd, from
src/dnd/DndProbe.tsx): that the drag started, and that the tab is its target. The phone is a 390x844 touch screen
(real touch events: hold, then move), the desktop a mouse. Every drag is cancelled (touchcancel, Escape) and every
write to the API is blocked, so nothing is ever published, copied or favourited.

    ~/.venvs/sm-room/bin/python tools/ui_evals/dnd_pickup.py            # phone and desktop
    ~/.venvs/sm-room/bin/python tools/ui_evals/dnd_pickup.py --phone    # one of them

What it caught first (24 Nov): a favourite and its copy in a date section had the same drag id, and when the copy's
section folded the favourite could no longer be picked up (SortableImageGrid `dragId`).

Exit 1 if any drag fails, 2 if the page has no bucket with favourites to try.
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

HOLD_MS = 350  # the page's touch drag starts after a 200 ms hold (src/pages/Index.tsx)

# in the page: the screens' tabs (a destination with a tab and a bucket that isn't headless), in tab order
TABS_JS = """async () => {
  const dests = await (await fetch('/api/publish-destinations')).json();
  const tabs = [...document.querySelectorAll('button[data-tab-id]')].map(b => b.dataset.tabId);
  return dests.filter(d => d.has_bucket && !d.headless && !d.hidden && tabs.includes(d.id)).map(d => d.id);
}"""


def state(page):
    return page.evaluate("window.__dnd ? {active: window.__dnd.active, over: window.__dnd.over} : null")


def centre(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


class Touch:
    """Real touch events through Chrome's input pipeline (the page sees touchstart/touchmove, as on a phone)."""

    def __init__(self, ctx, page):
        self.cdp = ctx.new_cdp_session(page)

    def send(self, kind, x=0, y=0):
        pts = [] if kind in ("touchEnd", "touchCancel") else [{"x": x, "y": y, "id": 0}]
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": pts})

    def start(self, page, x, y):
        self.send("touchStart", x, y)
        page.wait_for_timeout(HOLD_MS)
        self.send("touchMove", x + 2, y + 2)

    def move(self, page, x, y):
        self.send("touchMove", x, y)

    def cancel(self, page):
        self.send("touchCancel")


class Mouse:
    def start(self, page, x, y):
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 12, y + 12, steps=4)   # the page's mouse drag starts after 8 px

    def move(self, page, x, y):
        page.mouse.move(x, y)

    def cancel(self, page):
        page.keyboard.press("Escape")
        page.mouse.up()


def carry(page, hand, section, target):
    """Hold the first picture of `section`, carry it to `target`'s tab; returns (ok, words)."""
    card = page.locator(f"[data-section-variant={section}] .drag-grid > div").first
    if not card.count():
        return None, f"no picture in {section}"
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    sx, sy = centre(card.bounding_box())
    hand.start(page, sx, sy)
    page.wait_for_timeout(100)
    started = state(page) or {}
    if not started.get("active"):
        hand.cancel(page)
        return False, "did not start"
    tab = page.locator(f"button[data-tab-id='{target}']")
    tx, ty = centre(tab.bounding_box())
    for i in range(1, 16):
        x, y = sx + (tx - sx) * i / 15, sy + (ty - sy) * i / 15
        hand.move(page, x, y)
        page.wait_for_timeout(30)
    # the tab bar may pin as the page moves under the drag: aim at where the tab is now
    tx, ty = centre(tab.bounding_box())
    hand.move(page, tx, ty)
    page.wait_for_timeout(250)
    over = (state(page) or {}).get("over")
    hand.cancel(page)
    page.wait_for_timeout(300)
    left = (state(page) or {}).get("active")
    if over != f"drop-tab-{target}":
        return False, f"started ({started['active']}) but over {over!r}, not the {target} tab"
    if left:
        return False, "the drag did not end when cancelled"
    return True, f"{started['active'][:40]} -> {target}"


def run(url: str, phone: bool) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        opts = (dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
                if phone else dict(viewport={"width": 1400, "height": 1000}))
        ctx = browser.new_context(**opts)
        page = ctx.new_page()
        writes = []
        # nothing the drags do may reach the house: every write is blocked and counted
        page.route("**/api/**", lambda r: (writes.append(r.request.url), r.abort())
                   if r.request.method not in ("GET", "HEAD") else r.continue_())
        page.goto(url)
        page.wait_for_selector("button[data-tab-id]", timeout=30000)
        page.wait_for_timeout(1500)
        writes.clear()   # the page's own start-up calls

        screens = page.evaluate(TABS_JS)
        source = None
        for s in screens:
            page.locator(f"button[data-tab-id='{s}']").click()
            try:
                page.wait_for_selector("[data-section-variant=favourites] .drag-grid > div", timeout=8000)
                source = s
                break
            except Exception:
                continue
        name = "phone (touch)" if phone else "desktop (mouse)"
        if not source:
            print(f"{name}: no screen's bucket has favourites to try")
            browser.close()
            return 2
        target = next(s for s in screens if s != source)
        page.wait_for_timeout(1500)
        hand = Touch(ctx, page) if phone else Mouse()

        bad = 0
        for section in ("favourites", "dated"):
            ok, words = carry(page, hand, section, target)
            mark = "ok " if ok else ("-- " if ok is None else "BAD")
            bad += ok is False
            print(f"  {mark} {name:16} {source} {section:10} {words}")
        if writes:
            bad += 1
            print(f"  BAD {name:16} the drags tried to write: {writes}")
        browser.close()
        return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:5000/", help="this machine passes the sign-in gates (docs/ACCOUNTS.md)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--phone", action="store_true")
    group.add_argument("--desktop", action="store_true")
    a = ap.parse_args()
    modes = [True] if a.phone else [False] if a.desktop else [True, False]
    codes = [run(a.url, phone) for phone in modes]
    sys.exit(max(codes))


if __name__ == "__main__":
    main()
