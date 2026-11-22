"""Loope eval on a phone (owner, 22 Nov: "easy to get 'stuck' after pinch zooming & panning"; "doesn't cope with
landscape mode on a phone"). Drives the real page in headless Chromium as a 390x844 touch phone: opens a picture in a
screen's bucket, pinches in, pans, pinches out past fit, swipes on, closes while zoomed, turns the phone on its side,
double taps in and out, Escapes (zoom first, then the viewer), and closes with Back. It only views: no favourite,
publish or other action is touched.

    ~/.venvs/sm-room/bin/python tools/ui_evals/loope_zoom.py

Exit 1 if any step fails. Screenshots go to /tmp/loope_*.png.
"""
import sys
from playwright.sync_api import sync_playwright

S = "/tmp"
URL = "http://127.0.0.1:5000/"


def touch(cdp, kind, pts):
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(pts)]})


def pinch(cdp, page, cx, cy, d0, d1, steps=12):
    touch(cdp, "touchStart", [(cx - d0, cy), (cx + d0, cy)])
    for k in range(1, steps + 1):
        d = d0 + (d1 - d0) * k / steps
        touch(cdp, "touchMove", [(cx - d, cy), (cx + d, cy)])
        page.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(500)


def drag(cdp, page, x0, y0, x1, y1, steps=12):
    touch(cdp, "touchStart", [(x0, y0)])
    for k in range(1, steps + 1):
        touch(cdp, "touchMove", [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)])
        page.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(500)


STATE = """() => {
  const c = document.querySelector('.react-transform-component');
  const t = c ? getComputedStyle(c).transform : null;
  const m = t && t !== 'none' ? new DOMMatrix(t) : null;
  const dlg = document.querySelector('[role="dialog"]');
  const counter = dlg ? dlg.querySelector('.truncate')?.textContent : null;
  return { open: !!dlg, scale: m ? +m.a.toFixed(2) : 1, x: m ? Math.round(m.e) : 0, counter };
}"""

fails = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        fails.append(what)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.get_by_text("North screen", exact=True).first.click(timeout=20000)
    page.wait_for_selector("[data-section-variant] .drag-grid > div img", timeout=30000)
    page.wait_for_timeout(1500)
    # open the viewer on a dated picture (a tap on its tile)
    tile = page.locator('[data-section-variant="dated"] .drag-grid > div img').first
    tile.scroll_into_view_if_needed()
    tile.tap()
    page.wait_for_selector('[role="dialog"]', timeout=10000)
    page.wait_for_selector('.react-transform-component img', timeout=20000)
    page.wait_for_timeout(800)
    cdp = ctx.new_cdp_session(page)
    s0 = page.evaluate(STATE)
    check(s0["open"] and s0["scale"] == 1, f"opens at fit {s0}")
    page.screenshot(path=f"{S}/loope_portrait.png")

    stage = page.locator('.react-transform-wrapper').bounding_box()
    cx, cy = stage["x"] + stage["width"] / 2, stage["y"] + stage["height"] / 2
    pinch(cdp, page, cx, cy, 30, 140)
    s1 = page.evaluate(STATE)
    check(s1["scale"] > 1.5, f"pinch zooms in {s1}")
    drag(cdp, page, cx + 100, cy, cx - 100, cy)
    s2 = page.evaluate(STATE)
    check(s2["x"] != s1["x"] and s2["counter"] == s0["counter"], f"one finger pans, stays on the picture {s2}")
    page.screenshot(path=f"{S}/loope_zoomed.png")
    # pinch out hard: never below fit
    pinch(cdp, page, cx, cy, 160, 10)
    page.wait_for_timeout(600)
    s3 = page.evaluate(STATE)
    check(abs(s3["scale"] - 1) < 0.05, f"pinching out stops at fit {s3}")
    # at fit, a swipe goes to the next picture
    drag(cdp, page, cx + 120, cy, cx - 150, cy)
    page.wait_for_timeout(600)
    s4 = page.evaluate(STATE)
    check(s4["counter"] != s0["counter"], f"swipe at fit moves on {s0['counter']} -> {s4['counter']}")

    # zoom again, then the close button must work while zoomed
    pinch(cdp, page, cx, cy, 30, 140)
    check(page.evaluate(STATE)["scale"] > 1.5, "zoomed again")
    page.get_by_role("button", name="Close").tap()
    page.wait_for_timeout(600)
    check(not page.evaluate(STATE)["open"], "Close works while zoomed")

    # landscape phone
    tile.tap()
    page.wait_for_selector('.react-transform-component img', timeout=20000)
    page.set_viewport_size({"width": 844, "height": 390})
    page.wait_for_timeout(1000)
    img = page.locator('.react-transform-component img').bounding_box()
    check(img and img["height"] >= 300, f"landscape: the picture uses the height ({img and round(img['height'])} of 390)")
    page.screenshot(path=f"{S}/loope_landscape.png")
    # a tap hides the controls laid over it
    page.touchscreen.tap(422, 195)
    page.wait_for_timeout(700)
    page.screenshot(path=f"{S}/loope_landscape_tapped.png")
    # double tap zooms, double tap again back to fit
    page.touchscreen.tap(422, 195); page.wait_for_timeout(80); page.touchscreen.tap(422, 195)
    page.wait_for_timeout(600)
    d1 = page.evaluate(STATE)
    check(d1["scale"] > 2, f"double tap zooms {d1}")
    page.touchscreen.tap(422, 195); page.wait_for_timeout(80); page.touchscreen.tap(422, 195)
    page.wait_for_timeout(600)
    d2 = page.evaluate(STATE)
    check(abs(d2["scale"] - 1) < 0.05, f"double tap again: back to fit {d2}")
    # Escape steps out: zoom first, then the viewer
    page.touchscreen.tap(422, 195); page.wait_for_timeout(80); page.touchscreen.tap(422, 195)
    page.wait_for_timeout(500)
    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    e1 = page.evaluate(STATE)
    check(e1["open"] and abs(e1["scale"] - 1) < 0.05, f"Escape unzooms first {e1}")
    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    check(not page.evaluate(STATE)["open"], "Escape again closes")
    # Back closes it
    tile.tap()
    page.wait_for_selector('[role="dialog"]', timeout=10000)
    page.wait_for_timeout(500)
    page.go_back(); page.wait_for_timeout(800)
    check(not page.evaluate(STATE)["open"] and page.url.startswith(URL), f"Back closes it and stays on the page ({page.url})")
    check(not errors, f"no page errors {errors[:3]}")
    b.close()
sys.exit(1 if fails else 0)
