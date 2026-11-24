"""The TV page's layers, end to end in headless Chromium (owner, 24 Nov: "proper testing suites" for the publishing
pipeline; the page renders the screen's state document, art/screenState.ts).

A throwaway screen name gets a sequence of state documents, written as the publisher writes them
(output/<screen>.state.json), and the page is checked against each one, sampling the DOM every 100 ms throughout:

1. a still: it shows, no <video> exists;
2. the same still with its own clip: the still stays the base, the clip plays over it (one <video>, faded in, laid in
   the still's frame) and is let go (no <video>) once it has faded back;
3. a new still reached through a transition from the one shown: the transition plays (one <video>), the new still
   lands under it, and nothing is left once it has faded;
4. throughout: never more than one <video>, and the base is never the clip.

Fixtures are small re-encodes of real media under output/_uitest/ (made once, kept); the screen's documents and its
_screens folder are removed afterwards. It acts on nothing in the house: the screen name is not a configured screen.

    python tools/ui_evals/display_layers.py [--base http://127.0.0.1:5000] [--headed]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output"
FIX = OUT / "_uitest"
SCREEN = "uitest-layers"


def fixtures() -> dict:
    """Two stills (16:9) and a short clip and transition (960x540, 6 s), made from real media once."""
    FIX.mkdir(parents=True, exist_ok=True)
    want = {"a": FIX / "a.jpg", "b": FIX / "b.jpg", "clip": FIX / "clip.mp4", "transition": FIX / "transition.mp4"}
    if all(p.exists() for p in want.values()):
        return want
    stills = sorted((OUT / "north-screen").glob("2026*.jpg"))[-2:]
    for src, dst in zip(stills, (want["a"], want["b"])):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "scale=1920:1080", str(dst)], check=True)
    for dst, colour in ((want["clip"], "0x305070"), (want["transition"], "0x705030")):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=960x540:d=6:r=30,format=yuv420p",
                        "-vf", "drawbox=x='mod(t*120,900)':y=200:w=60:h=60:color=white:t=fill", "-c:v", "libx264",
                        "-preset", "veryfast", "-movflags", "+faststart", str(dst)], check=True)
    return want


def url(p: Path) -> str:
    return "/output/" + p.relative_to(OUT).as_posix()


def write(state: dict) -> None:
    tmp = OUT / f".{SCREEN}.state.json.tmp"
    tmp.write_text(json.dumps(state))
    tmp.replace(OUT / f"{SCREEN}.state.json")


def doc(seq: int, still: Path, **kw) -> dict:
    return {"v": 1, "screen": SCREEN, "seq": seq, "at": time.time(),
            "still": {"url": url(still), "width": 1920, "height": 1080, "origin": f"{SCREEN}/{still.name}"},
            "clip": None, "transition": None, "video": None, **kw}


SAMPLE = """() => {
  const vids = [...document.querySelectorAll('video')];
  const clip = document.querySelector('[data-sm-clip]');
  const imgs = [...document.querySelectorAll('[data-sm-art] img')].map(i => i.getAttribute('src'));
  return {t: performance.now(), videos: vids.length, srcs: vids.map(v => v.getAttribute('src')),
          clipOpacity: clip ? getComputedStyle(clip).opacity : null, imgs};
}"""


def run(base: str, headed: bool) -> list[str]:
    fx = fixtures()
    fails: list[str] = []
    samples: list[dict] = []

    def check(cond: bool, what: str) -> None:
        print(("ok    " if cond else "FAIL  ") + what, flush=True)
        if not cond:
            fails.append(what)

    write(doc(1, fx["a"]))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(f"{base}/display/{SCREEN}?kiosk=viewer", wait_until="domcontentloaded")

        def wait(pred, timeout_s: float) -> dict | None:
            end = time.time() + timeout_s
            while time.time() < end:
                s = page.evaluate(SAMPLE)
                samples.append(s)
                if pred(s):
                    return s
                page.wait_for_timeout(100)
            return None

        # 1. the still
        s = wait(lambda s: any(u and url(fx["a"]) in u for u in s["imgs"]), 15)
        check(bool(s), "1. the still is the base")
        check(bool(s) and s["videos"] == 0, "1. no <video> at rest")

        # 2. its clip, played over it and let go
        write(doc(2, fx["a"], clip={"url": url(fx["clip"]), "eased": True, "play_s": 6, "rest_s": [600, 600]}))
        s = wait(lambda s: s["videos"] == 1 and url(fx["clip"]) in s["srcs"][0], 35)
        check(bool(s), "2. the clip plays (one <video>)")
        s = wait(lambda s: s["clipOpacity"] == "1", 8)
        check(bool(s), "2. the clip fades in")
        check(bool(s) and any(u and url(fx["a"]) in u for u in s["imgs"]), "2. the still stays the base under it")
        s = wait(lambda s: s["videos"] == 0, 20)
        check(bool(s), "2. after it ends it fades back and the <video> is let go")
        s = wait(lambda s: s["videos"] > 0, 4)
        check(not s, "2. it rests (no new play straight away)")

        # 3. a new still through a transition from the one shown
        write(doc(3, fx["b"], transition={"url": url(fx["transition"]), "from": f"{SCREEN}/{fx['a'].name}",
                                          "to": f"{SCREEN}/{fx['b'].name}", "at": time.time()}))
        s = wait(lambda s: s["videos"] == 1 and url(fx["transition"]) in s["srcs"][0], 15)
        check(bool(s), "3. the transition plays (one <video>)")
        s = wait(lambda s: any(u and url(fx["b"]) in u for u in s["imgs"]) and s["videos"] == 0, 25)
        check(bool(s), "3. the new still lands and nothing is left playing")

        check(max(x["videos"] for x in samples) <= 1, f"4. never more than one <video> ({len(samples)} samples)")
        check(not any(url(fx["clip"]) in (u or "") for x in samples for u in x["imgs"]), "4. the clip is never the base")
        browser.close()
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    try:
        fails = run(a.base.rstrip("/"), a.headed)
    finally:
        (OUT / f"{SCREEN}.state.json").unlink(missing_ok=True)
        shutil.rmtree(OUT / "_screens" / SCREEN, ignore_errors=True)
    print(f"display_layers: {'PASS' if not fails else 'FAIL: ' + '; '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
