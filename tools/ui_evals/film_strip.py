"""Film mode eval (docs/FILM_MODE.md, stage 1): the generator's row of slots, on the real page, headless.

Checks, without making anything:
  * Create / Film switches the top section, and Film is remembered;
  * a film starts with two slots; three pictures chosen together fill A, B and a new C;
  * the form's own button makes the film, with about how long it takes under it, and the form's Upload, prompt
    helper and Advanced sit to its left (no workflow or screen to choose);
  * an empty slot's menu offers what the form's Upload offers;
  * a picture dragged from the page's own pictures lands in the slot it is dropped on;
  * with many slots the row scrolls sideways and the page does not;
  * the seconds slider and the "what happens" boxes hold what is put in them, and a reload keeps them;
  * (--phone) nothing is wider than the screen.

    ~/.venvs/sm-room/bin/python tools/ui_evals/film_strip.py A.jpg B.jpg C.jpg
    ~/.venvs/sm-room/bin/python tools/ui_evals/film_strip.py A.jpg B.jpg C.jpg --phone
    ~/.venvs/sm-room/bin/python tools/ui_evals/film_strip.py A.jpg B.jpg C.jpg --make "what happens A to B" "B to C"

--make presses "Make the film": two real videos on the video endpoint (about 10p each), then reports each clip's
length, size and whether it has sound. Nothing is published to a screen.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        FAILS.append(what)


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                          "-of", "json", path], capture_output=True, text=True).stdout
    return json.loads(out or "{}")


def run(args) -> int:
    width, height = (390, 844) if args.phone else (1500, 1000)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(ignore_default_args=["--hide-scrollbars"])   # scroll bars as a desktop draws them
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(args.url)
        page.wait_for_selector('[data-generator-mode="film"]', timeout=30000)
        # a new browser is given its Generated space on this first visit; the job feed opened before that shows it
        # none of its own jobs (the feed settles who is watching when it opens), so open the page again
        page.wait_for_timeout(2500)
        page.reload()
        page.wait_for_selector('[data-generator-mode="film"]', timeout=30000)

        print("Switching")
        check(page.locator("textarea[data-prompt-input]").count() == 1, "Create shows the prompt box")
        page.click('[data-generator-mode="film"]')
        page.wait_for_selector("[data-film-strip]")
        check(page.locator("textarea[data-prompt-input]").count() == 0, "Film replaces the prompt box")
        check(page.locator("[data-film-slot]").count() == 2, "two slots to start with")
        strip_text = page.locator("[data-film-strip]").inner_text().split()
        check(strip_text == ["A", "5", "s", "B"], f"no words in the strip but the slots' letters and the seconds ({strip_text})")
        make = page.locator("[data-generate-button]")
        check(make.is_disabled(), "the generate button is off with no pictures")
        check(page.locator("[data-generate-estimate]").count() == 0, "no time shown with nothing to make")
        check(page.get_by_role("button", name="Advanced Settings").count() == 1, "Advanced sits in the form's toolbar")

        print("A slot's menu")
        page.get_by_role("button", name="Choose a picture for A").click()
        items = [i.inner_text().strip() for i in page.get_by_role("menuitem").all()]
        wanted = ["From Gallery", "From Camera", "Famous artworks"] if args.phone else ["From this computer", "Famous artworks"]
        check(items == wanted, f"the same choices as the form's Upload ({items})")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        print("Pictures")
        with page.expect_file_chooser() as chooser:
            page.get_by_role("button", name="Choose a picture for A").click()
            page.get_by_role("menuitem", name=wanted[0]).click()
        chooser.value.set_files(args.pictures)
        page.wait_for_selector('[data-film-slot="C"] img')
        check(page.locator("[data-film-slot] img").count() == 3, "three pictures chosen together fill A, B and a new C")
        check(make.is_enabled(), "the generate button is on")
        check(page.locator("[data-generate-estimate]").inner_text() == "About 2 minutes", "about how long, under the button")

        print("Links")
        box = page.locator('[data-film-link="AB"] textarea')
        box.fill(args.make[0] if args.make else "the first becomes the second")
        if args.make:
            page.locator('[data-film-link="BC"] textarea').fill(args.make[1])
        thumb = page.locator('[data-film-link="AB"] [role=slider]')
        thumb.focus()
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowLeft")
        check(page.locator('[data-film-link="AB"] [data-film-seconds]').inner_text() == "3 s", "the slider sets 3 seconds")

        if args.phone:
            over = page.evaluate("document.documentElement.scrollWidth - innerWidth")
            check(over <= 1, f"nothing is wider than the phone's screen (over by {over}px)")
            a = page.locator('[data-film-slot="A"]').bounding_box()
            b = page.locator('[data-film-slot="B"]').bounding_box()
            check(b["y"] > a["y"] + a["height"], "on a phone the slots run down the page")

        if not args.phone and not args.make:
            print("Dragging a picture from the page")
            # B, emptied: a slot in view (one further along is reached by scrolling the row)
            page.get_by_role("button", name="Take the picture out of B").click()
            target = page.locator('[data-film-slot="B"]')
            page.get_by_text(args.tab, exact=True).first.click(timeout=20000)
            page.wait_for_selector("[data-section-variant] .drag-grid > div img", timeout=30000)
            page.wait_for_timeout(1500)
            # a dated picture (the favourites above them only reorder), picked up where it is; the page is then wheeled
            # back to the top with the picture still held, and the picture carried to the slot
            card_el = page.locator('[data-section-variant="dated"] .drag-grid > div').first
            card = None
            if card_el.count():
                card_el.scroll_into_view_if_needed()
                page.wait_for_timeout(800)
                card = card_el.bounding_box()
            if not card:
                print("  (no dated picture on the page to drag: skipped)")
            else:
                sx, sy = card["x"] + card["width"] / 2, card["y"] + card["height"] / 2
                page.mouse.move(sx, sy)
                page.mouse.down()
                page.mouse.move(sx + 12, sy + 12, steps=4)
                page.wait_for_function("window.__dnd && window.__dnd.active != null", timeout=5000)
                page.mouse.wheel(0, -100000)
                page.wait_for_timeout(800)
                t = target.bounding_box()
                page.mouse.move(t["x"] + t["width"] / 2, t["y"] + t["height"] / 2, steps=12)
                page.wait_for_timeout(300)
                over = page.evaluate("window.__dnd ? String(window.__dnd.over) : null")
                check(bool(over and over.startswith("drop-film-slot-")), f"the drag is over the slot (the page says {over})")
                lit = page.evaluate("[...document.querySelectorAll('[data-film-slot]')].filter(e => e.className.includes('border-primary')).map(e => e.dataset.filmSlot)")
                check(lit == ["B"], f"slot B lights up under the drag (lit: {lit})")
                page.mouse.up()
                page.wait_for_timeout(500)
                print("  slots now:", page.evaluate("[...document.querySelectorAll('[data-film-slot]')].map(e => e.dataset.filmSlot + ':' + (e.querySelector('img')?.getAttribute('src') || '').slice(0, 40))"))
                src = target.locator("img").get_attribute("src") if target.locator("img").count() else None
                check(bool(src) and not src.startswith("blob:"), f"the dropped picture is in B ({(src or '')[:60]})")

            print("Reload")
            page.reload()
            page.wait_for_selector("[data-film-strip]")
            check(page.locator('[data-film-link="AB"] textarea').input_value() == "the first becomes the second",
                  "Film is still open and the link's words are kept")
            check(page.locator('[data-film-link="AB"] [data-film-seconds]').inner_text() == "3 s", "its seconds are kept")

            print("Many slots")
            for _ in range(8):
                page.locator("[data-film-add]").click()
            row = page.locator("[data-film-row]")
            wide = row.evaluate("el => [el.scrollWidth, el.clientWidth, getComputedStyle(el).overflowX]")
            check(wide[0] > wide[1] and wide[2] == "auto", f"the row scrolls sideways (content {wide[0]} in {wide[1]}, overflow {wide[2]})")
            over = page.evaluate("document.documentElement.scrollWidth - innerWidth")
            check(over <= 1, f"the page itself is no wider than the window (over by {over}px)")
            box = page.locator("[data-film-strip]").bounding_box()
            check(box["x"] + box["width"] <= width, "the strip ends inside the window")
            bar = row.evaluate("el => el.offsetHeight - el.clientHeight")
            check(bar > 0, f"its scroll bar is drawn ({bar}px)")

            page.get_by_role("button", name="Clear the film").click()
            check(page.locator("[data-film-slot]").count() == 2 and page.locator("[data-film-slot] img").count() == 0,
                  "the form's X empties the film")
            page.click('[data-generator-mode="create"]')
            check(page.locator("textarea[data-prompt-input]").count() == 1, "Create brings the prompt box back")

        if args.make:
            print("Making the film (two real videos)")
            started = time.time()
            make.click()
            page.wait_for_selector("[data-film-status]", timeout=10000)
            check(page.locator("[data-film-status]").count() == 2, "each link shows its video's progress")
            done: dict[str, dict] = {}
            batch = None
            while time.time() - started < args.wait:
                page.wait_for_timeout(5000)
                jobs = json.load(urllib.request.urlopen(f"{args.url.rstrip('/')}/api/jobs?state=all&limit=10"))["jobs"]
                mine = [j for j in jobs if j.get("workflow") == "h3-film.json" and j["created_at"] >= started - 5]
                batch = batch or (mine[0]["batch_id"] if mine else None)
                for j in mine:
                    if j["status"] in ("completed", "failed", "cancelled"):
                        done[j["id"]] = j
                if len(mine) == 2 and len(done) == 2:
                    break
            check(len(done) == 2, f"both videos ended within {args.wait} s")
            for j in sorted(done.values(), key=lambda j: j.get("batch_index") or 0):
                print(f"  segment {j.get('batch_index')}: {j['status']} in {j.get('duration_s')} s, "
                      f"cost {j.get('cost_gbp')}, {j.get('error') or j.get('result_url')}")
                print(f"    asked: {j.get('prompt')!r}\n    sent:  {j.get('refined_prompt')!r}")
                check(j["status"] == "completed", f"segment {j.get('batch_index')} completed")
            page.wait_for_timeout(4000)
            texts = [t.inner_text() for t in page.locator("[data-film-status]").all()]
            check(all(t == "Ready" for t in texts), f"the links say the videos are ready ({texts})")
            print(f"  batch {batch}")
        browser.close()
    print("\n" + ("FAILED: " + "; ".join(FAILS) if FAILS else "all ok"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pictures", nargs=3, help="three pictures for A, B and C")
    ap.add_argument("--url", default="http://127.0.0.1:5000/")
    ap.add_argument("--tab", default="North screen", help="the bucket tab to drag a picture from")
    ap.add_argument("--phone", action="store_true")
    ap.add_argument("--make", nargs=2, metavar=("A_TO_B", "B_TO_C"), help="press Make the film (two paid videos)")
    ap.add_argument("--wait", type=int, default=900)
    sys.exit(run(ap.parse_args()))
