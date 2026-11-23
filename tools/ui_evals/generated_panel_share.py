"""Run: ~/.venvs/sm-room/bin/python tools/ui_evals/generated_panel_share.py. Generated panel buttons + Loope Share on a phone. Views only: navigator.share is stubbed, nothing is sent."""
import sys
from playwright.sync_api import sync_playwright
S = sys.argv[1] if len(sys.argv) > 1 else '/tmp'
fails = []
def check(c, w):
    print(("ok   " if c else "FAIL ") + w)
    if not c: fails.append(w)
TOP = "([x,y]) => { const e = document.elementFromPoint(x,y); return e ? e.tagName + ' ' + (e.getAttribute('aria-label')||'') : null }"
with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    ctx.add_init_script("""
      window.__shared = [];
      navigator.canShare = (d) => !!(d && d.files && d.files.length);
      navigator.share = async (d) => { window.__shared.push(d.files.map(f => [f.name, f.type, f.size])); };
    """)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(lambda u: "/buckets/_recent/complete" in u, lambda r: r.continue_(url="http://127.0.0.1:5000/api/buckets/_recent/complete?everyone=1"))
    page.goto("http://127.0.0.1:5000/")
    btn = page.get_by_role("button", name="Open in the viewer", exact=True).first
    btn.wait_for(timeout=30000)
    btn.scroll_into_view_if_needed(); page.mouse.wheel(0, 150); page.wait_for_timeout(800)
    bb = btn.bounding_box()
    media = page.locator("[class*=selectedImageContainer] video, [class*=selectedImageContainer] img").first.bounding_box()
    check(bb["width"] >= 40, f"button is 40px {bb}")
    check(bb["y"] < media["y"] + 60 and bb["x"] + bb["width"] <= media["x"] + media["width"] + 1, f"button on the picture's top right {bb} vs {media}")
    check(page.evaluate(TOP, [bb["x"] + 20, bb["y"] + 20]) == "BUTTON Open in the viewer", "nothing covers it")
    check(page.evaluate(TOP, [media["x"] + 30, media["y"] + media["height"] - 25]).startswith(("VIDEO", "IMG")), "the clip's controls are not covered")
    page.screenshot(path=f"{S}/g3.png")
    btn.tap()
    page.wait_for_selector('[role="dialog"]', timeout=10000)
    page.wait_for_timeout(1000)
    check(True, "Loope opens from it")
    page.screenshot(path=f"{S}/g4.png")
    page.get_by_role("button", name="Share", exact=True).tap()
    page.wait_for_function("window.__shared.length > 0", timeout=30000)
    sh = page.evaluate("window.__shared")
    print(sh)
    check(sh[0][0][1] in ("video/mp4", "image/jpeg", "image/png") and sh[0][0][2] > 10000, f"Share hands over the file itself {sh}")
    check(not errors, f"no page errors {errors[:3]}")
    b.close()
sys.exit(1 if fails else 0)
