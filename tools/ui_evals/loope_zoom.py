"""Loope eval on a phone (owner, 22-23 Nov: "easy to get 'stuck' after pinch zooming & panning"; "doesn't cope with
landscape mode on a phone"; "pinch zoom doesn't work on video clips"; "image should remain stably located on screen in
transition from loope to view mode"). Drives the real page in headless Chromium as a 390x844 touch phone: opens a
picture in a screen's bucket, pinches it into the zoom view (checking it doesn't jump), pans, pinches out to leave,
swipes on, closes the zoom and the viewer with their buttons, turns the phone on its side, taps the controls away,
double taps in and back to fit, taps the black to leave, Escapes (zoom first, then the viewer), closes with Back, and
zooms a clip if the bucket has one. It only views: no favourite, publish or other action is touched.

    ~/.venvs/sm-room/bin/python tools/ui_evals/loope_zoom.py

Exit 1 if any step fails. Screenshots go to /tmp/loope_*.png.
"""
import sys
from playwright.sync_api import sync_playwright

S = "/tmp"
URL = "http://127.0.0.1:5000/"


def touch(cdp, kind, pts):
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(pts)]})


def pinch(cdp, page, cx, cy, d0, d1, steps=12, probe=None):
    touch(cdp, "touchStart", [(cx - d0, cy), (cx + d0, cy)])
    page.wait_for_timeout(50)
    got = page.evaluate(probe) if probe else None
    for k in range(1, steps + 1):
        d = d0 + (d1 - d0) * k / steps
        touch(cdp, "touchMove", [(cx - d, cy), (cx + d, cy)])
        page.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(600)
    return got


def drag(cdp, page, x0, y0, x1, y1, steps=12):
    touch(cdp, "touchStart", [(x0, y0)])
    for k in range(1, steps + 1):
        touch(cdp, "touchMove", [(x0 + (x1 - x0) * k / steps, y0 + (y1 - y0) * k / steps)])
        page.wait_for_timeout(16)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(600)


def double_tap(page, x, y):
    page.touchscreen.tap(x, y); page.wait_for_timeout(80); page.touchscreen.tap(x, y)
    page.wait_for_timeout(700)


# zoom: the zoom view's transform (none when it is shut); media: the picture in view in the carousel
STATE = """() => {
  const c = document.querySelector('.react-transform-component');
  const t = c ? getComputedStyle(c).transform : null;
  const m = t && t !== 'none' ? new DOMMatrix(t) : null;
  const dlg = document.querySelector('[role="dialog"]');
  const counter = dlg ? dlg.querySelector('.truncate')?.textContent : null;
  const w = innerWidth, h = innerHeight;
  const media = [...document.querySelectorAll('[role="dialog"] [data-loope-media]')].map(e => e.getBoundingClientRect())
    .find(r => r.width > 2 && r.left < w / 2 && r.right > w / 2);
  return { open: !!dlg, zoom: !!c, scale: m ? +m.a.toFixed(2) : 1, x: m ? Math.round(m.e) : 0, counter,
           media: media ? { x: Math.round(media.left), y: Math.round(media.top), w: Math.round(media.width), h: Math.round(media.height) } : null };
}"""

# where the zoom view's copy is drawn on screen, the moment it opens
ZOOM_RECT = """() => {
  const e = document.querySelector('.react-transform-component [data-zoom-media]');
  if (!e) return null;
  const r = e.getBoundingClientRect();
  return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
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
    page.wait_for_selector('[role="dialog"] img[data-loope-media]', timeout=20000)
    page.wait_for_timeout(1200)
    cdp = ctx.new_cdp_session(page)
    s0 = page.evaluate(STATE)
    check(s0["open"] and not s0["zoom"] and s0["media"], f"opens, not zoomed {s0}")
    page.screenshot(path=f"{S}/loope_portrait.png")

    m = s0["media"]
    cx, cy = m["x"] + m["w"] / 2, m["y"] + m["h"] / 2
    first = pinch(cdp, page, cx, cy, 30, 140, probe=ZOOM_RECT)
    drift = max(abs(first[k] - m[k]) for k in "xywh") if first else None
    check(drift is not None and drift <= 2, f"the zoom opens exactly over the picture (drift {drift} px)")
    s1 = page.evaluate(STATE)
    check(s1["zoom"] and s1["scale"] > 1.5, f"pinch zooms in {s1}")
    drag(cdp, page, 195 + 100, 422, 195 - 100, 422)
    s2 = page.evaluate(STATE)
    check(s2["zoom"] and s2["x"] != s1["x"] and s2["counter"] == s0["counter"], f"one finger pans, stays on the picture {s2}")
    page.screenshot(path=f"{S}/loope_zoomed.png")
    # pinch out well below fit and let go: the zoom closes, the viewer stays
    pinch(cdp, page, 195, 422, 170, 10)
    page.wait_for_timeout(400)
    s3 = page.evaluate(STATE)
    check(s3["open"] and not s3["zoom"], f"pinching out leaves the zoom {s3}")
    # a swipe goes to the next picture
    drag(cdp, page, cx + 120, cy, cx - 150, cy)
    s4 = page.evaluate(STATE)
    check(s4["counter"] != s0["counter"], f"swipe moves on {s0['counter']} -> {s4['counter']}")

    # zoom again: its close button, then the viewer's
    m = s4["media"] or m
    pinch(cdp, page, m["x"] + m["w"] / 2, m["y"] + m["h"] / 2, 30, 140)
    check(page.evaluate(STATE)["scale"] > 1.5, "zoomed again")
    page.get_by_role("button", name="Close zoom").tap()
    page.wait_for_timeout(600)
    s5 = page.evaluate(STATE)
    check(s5["open"] and not s5["zoom"], f"the zoom's X closes it {s5}")
    page.get_by_role("button", name="Close", exact=True).tap()
    page.wait_for_timeout(600)
    check(not page.evaluate(STATE)["open"], "Close shuts the viewer")

    # landscape phone
    tile.tap()
    page.wait_for_selector('[role="dialog"] img[data-loope-media]', timeout=20000)
    page.set_viewport_size({"width": 844, "height": 390})
    page.wait_for_timeout(1000)
    sl = page.evaluate(STATE)
    check(sl["media"] and sl["media"]["h"] >= 300, f"landscape: the picture uses the height ({sl['media']} of 390)")
    page.screenshot(path=f"{S}/loope_landscape.png")
    # a tap hides the controls laid over it
    page.touchscreen.tap(422, 195)
    page.wait_for_timeout(700)
    page.screenshot(path=f"{S}/loope_landscape_tapped.png")
    # double tap zooms in; double tap again goes back to fit
    double_tap(page, 422, 195)
    d1 = page.evaluate(STATE)
    check(d1["zoom"] and d1["scale"] > 2, f"double tap zooms {d1}")
    double_tap(page, 422, 195)
    d2 = page.evaluate(STATE)
    check(d2["zoom"] and abs(d2["scale"] - 1) < 0.05, f"double tap again: back to fit {d2}")
    # at fit a landscape phone has black at the sides: a tap there leaves
    page.touchscreen.tap(12, 195)
    page.wait_for_timeout(800)
    d3 = page.evaluate(STATE)
    check(d3["open"] and not d3["zoom"], f"a tap on the black leaves the zoom {d3}")
    # Escape steps out: zoom first, then the viewer
    double_tap(page, 422, 195)
    check(page.evaluate(STATE)["zoom"], "zoomed for Escape")
    page.keyboard.press("Escape"); page.wait_for_timeout(600)
    e1 = page.evaluate(STATE)
    check(e1["open"] and not e1["zoom"], f"Escape leaves the zoom first {e1}")
    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    check(not page.evaluate(STATE)["open"], "Escape again closes")
    # Back closes it
    page.set_viewport_size({"width": 390, "height": 844})
    tile.tap()
    page.wait_for_selector('[role="dialog"]', timeout=10000)
    page.wait_for_timeout(500)
    page.go_back(); page.wait_for_timeout(800)
    check(not page.evaluate(STATE)["open"] and page.url.startswith(URL), f"Back closes it and stays on the page ({page.url})")

    # a clip, if the bucket has one: arrow through the viewer to it and pinch it
    tile.tap()
    page.wait_for_selector('[role="dialog"]', timeout=10000)
    clip = None
    for _ in range(40):
        clip = page.evaluate("""() => {
          const v = [...document.querySelectorAll('[role="dialog"] video[data-loope-media]')]
            .find(e => { const r = e.getBoundingClientRect(); return r.width > 2 && r.left < innerWidth / 2 && r.right > innerWidth / 2; });
          if (!v) return null;
          const r = v.getBoundingClientRect();
          return { ready: v.readyState, x: r.left + r.width / 2, y: r.top + r.height / 3 };
        }""")
        if clip:
            break
        page.keyboard.press("ArrowRight"); page.wait_for_timeout(250)
    if not clip:
        print("skip no clip in the first 40 slides")
    else:
        page.wait_for_function("""() => [...document.querySelectorAll('[role="dialog"] video[data-loope-media]')]
          .some(v => v.readyState >= 1 && v.getBoundingClientRect().left >= 0 && v.getBoundingClientRect().left < innerWidth / 2)""", timeout=30000)
        pinch(cdp, page, clip["x"], clip["y"], 30, 140)
        c1 = page.evaluate(STATE)
        has_video = page.evaluate("() => !!document.querySelector('.react-transform-component video[data-zoom-media]')")
        check(c1["zoom"] and c1["scale"] > 1.5 and has_video, f"a clip pinches into the zoom {c1}")
        page.screenshot(path=f"{S}/loope_clip_zoomed.png")
        page.keyboard.press("Escape"); page.wait_for_timeout(600)
        check(not page.evaluate(STATE)["zoom"], "Escape leaves the clip's zoom")
    check(not errors, f"no page errors {errors[:3]}")
    b.close()
sys.exit(1 if fails else 0)
