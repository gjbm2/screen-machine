"""Snapshotter: renders an ArtState in the display page's art-only mode (LIVE_V1 §2.3).

One persistent headless page per screen at `{art_page_base}/display/<id>?artonly=1`
at the display's own size (its destination's maxwidth x maxheight, e.g. 2160x3840 for a
portrait screen; else snapshot.viewport_w x viewport_h, 3840x2160), DPR 1. `snapshot(screen, state)`:

    await window.__smArtReady  ->  await window.__smRenderArt(state)  ->  CDP Page.captureScreenshot(png)

The capture uses CDP's `optimizeForSpeed` PNG encoding: still lossless and
pixel-identical, but measured at ~0.9-1.5 s for a 4K photo where the default
encoder (and Playwright's page.screenshot) took ~3.5 s, over the 3 s budget.

Clip bases (kind "video"): Chrome for Testing can't be trusted to decode the
clip, so the frame at `clip_t` is extracted with ffmpeg, served to the page via a
Playwright route on the page's own origin, and rendered as an image state.

Pages are reused; a crashed or wedged page is dropped and recreated (in the
background, so the next handshake finds it warm). Each snapshot is bounded by
`snapshot.timeout_s` (3 s). Loading a page (a cold Chrome takes ~3-4 s here) is not
part of that budget: the screens call `ensure_ready` before they freeze the page.
`prefetch` renders a page's current still ahead of time (from kiosk.state), so the
4K decode is cached before a cut needs it.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import math
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urljoin, urlsplit

from .util import describe_exc, fit_pixels, spawn, viewport_of

if TYPE_CHECKING:
    from .config import RoomConfig

log = logging.getLogger("room_service.snapshot")


class SnapshotError(Exception):
    pass


# ---------------------------------------------------------------------------
# ArtState validation. The state comes from the page's `ack frozen` (and kiosk.state for a
# prefetch): input from a LAN client, which drives a headless Chromium (and ffmpeg) on the
# workstation. base_url is therefore checked by path against an allowlist (DCL v3 §7):
# only a still/clip under /output/, relative, or absolute on the configured art page or
# Flask origin. The result is always the relative path, which the art-only page resolves
# against art_page_base (clips: against Flask). The rest of the state is clamped.
# ---------------------------------------------------------------------------

_OUTPUT_PATH = re.compile(r"/output/[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?", re.ASCII)
_QUERY = re.compile(r"[A-Za-z0-9_.=&%-]{0,200}", re.ASCII)
_HEX = re.compile(r"#[0-9A-Fa-f]{6}", re.ASCII)


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def safe_base_url(url: Any, allowed_origins: set[str]) -> str:
    """'/output/<name>[/<name>][?query]', or SnapshotError. A plain query is kept: it is the
    page's cache-buster (`?t=<ms>`), without which a changed still could be served stale
    from the snapshot page's cache. The fragment is dropped."""
    if not isinstance(url, str) or not url or len(url) > 1024:
        raise SnapshotError("ArtState base_url missing or too long")
    parts = urlsplit(url)
    if (parts.scheme or parts.netloc) and origin_of(url) not in allowed_origins:
        raise SnapshotError("ArtState base_url: origin not allowed (only /output/ paths)")
    path = parts.path
    if not _OUTPUT_PATH.fullmatch(path) or any(not seg.strip(".") for seg in path.split("/")[2:]):
        raise SnapshotError("ArtState base_url: only /output/<file> paths are allowed")
    query = parts.query if _QUERY.fullmatch(parts.query or "") else ""
    return path + (f"?{query}" if query else "")


def _unit(v: Any, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if not math.isfinite(f) else min(1.0, max(0.0, f))


def safe_art_state(state: Any, allowed_origins: set[str]) -> dict:
    """A validated, normalised copy of an ArtState (LIVE_V1 §2.1). Raises SnapshotError."""
    if not isinstance(state, dict):
        raise SnapshotError("ArtState must be an object")
    kind = "video" if state.get("kind") == "video" else "image"
    clip_t = None
    if kind == "video":
        try:
            clip_t = float(state.get("clip_t") or 0.0)
        except (TypeError, ValueError):
            clip_t = 0.0
        clip_t = min(86400.0, max(0.0, clip_t)) if math.isfinite(clip_t) else 0.0
    mask = state.get("mask") if isinstance(state.get("mask"), dict) else {}
    hexv = mask.get("warm_hex")
    flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
    vp = state.get("viewport") if isinstance(state.get("viewport"), dict) else {}

    def dim(v: Any) -> int | None:
        return int(v) if isinstance(v, (int, float)) and math.isfinite(v) and 0 < v <= 16384 else None

    return {
        "base_url": safe_base_url(state.get("base_url"), allowed_origins),
        "kind": kind, "clip_t": clip_t,
        "mask": {"brightness": _unit(mask.get("brightness"), 1.0),
                 "warm_hex": hexv.upper() if isinstance(hexv, str) and _HEX.fullmatch(hexv) else "#FFFFFF",
                 "warm_alpha": _unit(mask.get("warm_alpha"), 0.0)},
        "flags": {"nomask": flags.get("nomask") is True, "nocolour": flags.get("nocolour") is True,
                  "fit": "fill" if flags.get("fit") == "fill" else "fit"},
        "viewport": {"w": dim(vp.get("w")), "h": dim(vp.get("h"))},
    }


class Snapshotter(Protocol):
    async def start(self, screens: list[str]) -> None: ...
    async def ensure_ready(self, screen: str) -> None: ...      # before a freeze: page loaded
    async def snapshot(self, screen: str, state: dict) -> bytes: ...
    def prefetch(self, screen: str, state: dict) -> None: ...   # best effort: warm the decode
    async def close(self) -> None: ...
    def status(self) -> dict: ...


# The clip URL comes from the page's ack (untrusted input): ffmpeg may only fetch it over
# HTTP(S). No file:, concat:, subfile: etc., and nothing a playlist could redirect into.
CLIP_PROTOCOLS = "http,https,tcp,tls"


async def extract_clip_frame(ffmpeg: str, url: str, t: float, timeout_s: float) -> bytes:
    """The frame of `url` at `t` seconds, as PNG bytes (input seek: frame-accurate when decoding)."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise SnapshotError("clip base must be an http(s) URL")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", CLIP_PROTOCOLS,
        "-ss", f"{max(0.0, t):.3f}", "-i", url, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    if proc.returncode != 0 or not out.startswith(b"\x89PNG"):
        raise SnapshotError(f"ffmpeg frame extract failed (rc={proc.returncode}): "
                            f"{err.decode(errors='replace')[-200:].strip()}")
    return out


class PlaywrightSnapshotter:
    def __init__(self, cfg: "RoomConfig") -> None:
        self.cfg = cfg
        self.scfg = cfg.snapshot
        self.base = cfg.art_page_base.rstrip("/")
        parts = urlsplit(self.base)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.clip_base = (self.scfg.clip_base or cfg.flask_url).rstrip("/") + "/"
        self.allowed_origins = {origin_of(self.base), origin_of(cfg.flask_url)}
        self._pw: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}
        self._contexts: dict[str, Any] = {}
        self._cdp: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._browser_lock = asyncio.Lock()
        self._frames: dict[str, bytes] = {}
        self._frame_ids = itertools.count(1)
        self._warm_tasks: dict[str, asyncio.Task] = {}
        self._bg: set[asyncio.Task] = set()
        self._closing = False
        self.last_ms: dict[str, float] = {}
        self.last_error: dict[str, str | None] = {}
        self.count = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self, screens: list[str]) -> None:
        """Warm pages for `screens` in the background (never blocks service start)."""
        if self.scfg.warm:
            for s in screens:
                self._warm(s)

    def _warm(self, screen: str) -> None:
        t = self._warm_tasks.get(screen)
        if self._closing or (t and not t.done()):
            return

        async def go() -> None:
            try:
                async with self._lock(screen):
                    await self._ensure_page(screen)
            except Exception as e:
                self.last_error[screen] = describe_exc(e)
                log.warning("snapshot page for %s not ready: %s", screen, self.last_error[screen])

        self._warm_tasks[screen] = spawn(go(), f"snapshot-warm-{screen}")

    async def close(self) -> None:
        self._closing = True
        tasks = [*self._warm_tasks.values(), *self._bg]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for s in list(self._pages):
            await self._drop_page(s)
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def status(self) -> dict:
        return {"browser": bool(self._browser and self._browser.is_connected()),
                "pages": sorted(s for s, p in self._pages.items() if not p.is_closed()),
                "last_ms": dict(self.last_ms), "last_error": dict(self.last_error), "count": self.count}

    def _lock(self, screen: str) -> asyncio.Lock:
        return self._locks.setdefault(screen, asyncio.Lock())

    async def ensure_ready(self, screen: str) -> None:
        """Load (or reload) the screen's art-only page; bounded by page_load_timeout_s."""
        try:
            async with self._lock(screen):
                await asyncio.wait_for(self._ensure_page(screen), self.scfg.page_load_timeout_s + 5)
        except Exception as e:
            self.last_error[screen] = describe_exc(e)
            raise SnapshotError(f"art-only page not ready: {self.last_error[screen]}") from None

    def prefetch(self, screen: str, state: dict) -> None:
        """Render `state` without a screenshot so its image is decoded and cached. Skipped
        while the page is busy; never blocks a snapshot for long."""
        lock = self._lock(screen)
        page = self._pages.get(screen)
        if (self._closing or lock.locked() or page is None or page.is_closed()
                or state.get("kind") == "video"):
            return
        try:
            state = safe_art_state(state, self.allowed_origins)
        except SnapshotError:
            return

        async def go() -> None:
            try:
                async with lock:
                    await asyncio.wait_for(page.evaluate("s => window.__smRenderArt(s)", state), 3.0)
            except Exception as e:
                if not self._closing:
                    log.info("prefetch for %s failed (%s); recreating the page", screen, describe_exc(e))
                    await self._recycle(screen)

        t = spawn(go(), f"snapshot-prefetch-{screen}")
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # -- browser and pages -------------------------------------------------
    async def _ensure_browser(self) -> Any:
        async with self._browser_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            from playwright.async_api import async_playwright   # imported lazily: tests use fakes
            if self._pw is None:
                self._pw = await async_playwright().start()
            exe = str(Path(self.scfg.executable_path).expanduser())
            log.info("launching headless Chrome for snapshots (%s)", exe)
            self._browser = await self._pw.chromium.launch(
                executable_path=exe, headless=True, args=list(self.scfg.headless_args))
            self._pages.clear()
            self._contexts.clear()
            return self._browser

    def viewport(self, screen: str, state: dict | None = None) -> dict[str, int]:
        """The glass's shape, so the anchor matches the live render: the page's reported viewport at the
        display's pixel count, else the display's own size (maxwidth x maxheight), else the default."""
        sc = self.cfg.screens.get(screen)
        if sc is not None and sc.width and sc.height:
            base = {"width": int(sc.width), "height": int(sc.height)}
        else:
            base = {"width": self.scfg.viewport_w, "height": self.scfg.viewport_h}
        glass = viewport_of(state)
        return fit_pixels(*glass, base["width"] * base["height"]) if glass else base

    def page_url(self, screen: str) -> str:
        return f"{self.base}/display/{screen}?artonly=1"

    async def _ensure_page(self, screen: str, state: dict | None = None) -> Any:
        page = self._pages.get(screen)
        if page is not None and not page.is_closed() and not getattr(page, "_sm_crashed", False):
            if viewport_of(state) is not None and hasattr(page, "set_viewport_size"):
                vp = self.viewport(screen, state)
                if getattr(page, "viewport_size", None) != vp:
                    await page.set_viewport_size(vp)      # the glass changed shape (a resized browser window)
            return page
        await self._drop_page(screen)
        browser = await self._ensure_browser()
        ctx = await browser.new_context(
            viewport=self.viewport(screen, state),
            device_scale_factor=1, color_scheme="dark")
        page = await ctx.new_page()
        page._sm_crashed = False

        def on_crash(_p: Any = None) -> None:
            page._sm_crashed = True
            log.warning("snapshot page for %s crashed; it will be recreated", screen)

        page.on("crash", on_crash)
        await page.route(f"{self.origin}/__sm_snapshot/**", self._serve_frame)
        ms = self.scfg.page_load_timeout_s * 1000
        await page.goto(self.page_url(screen), wait_until="load", timeout=ms)
        await page.wait_for_function("window.__smArtReady === true", timeout=ms)
        self._cdp[screen] = await ctx.new_cdp_session(page)
        self._pages[screen] = page
        self._contexts[screen] = ctx
        log.info("snapshot page ready for %s (%s)", screen, self.page_url(screen))
        return page

    async def _drop_page(self, screen: str) -> None:
        ctx = self._contexts.pop(screen, None)
        self._pages.pop(screen, None)
        self._cdp.pop(screen, None)
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass

    async def _serve_frame(self, route: Any) -> None:
        key = route.request.url.rsplit("/", 1)[-1].split("?", 1)[0]
        body = self._frames.get(key)
        if body is None:
            await route.fulfill(status=404, body=b"")
        else:
            await route.fulfill(status=200, content_type="image/png", body=body,
                                headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})

    # -- snapshot ----------------------------------------------------------
    async def snapshot(self, screen: str, state: dict) -> bytes:
        t0 = time.monotonic()
        stage: list[str] = ["start"]
        try:
            state = safe_art_state(state, self.allowed_origins)
        except SnapshotError as e:
            self.last_error[screen] = str(e)
            log.warning("%s: refused ArtState: %s", screen, e)
            raise
        try:
            png = await asyncio.wait_for(self._snapshot(screen, state, stage), self.scfg.timeout_s)
        except asyncio.TimeoutError:
            self.last_error[screen] = f"timeout ({stage[0]})"
            if stage[0] != "capture":
                # A render that never resolved wedges the page's serialised render chain:
                # rebuild it. A slow capture (Chrome's 4K PNG encode on a busy CPU) leaves the
                # page healthy, and a reload would only make the next snapshot colder.
                await self._recycle(screen)
            raise SnapshotError(f"snapshot timed out after {self.scfg.timeout_s:g} s ({stage[0]})") from None
        except SnapshotError as e:
            self.last_error[screen] = str(e)
            raise
        except Exception as e:
            self.last_error[screen] = describe_exc(e)
            await self._recycle(screen)
            raise SnapshotError(self.last_error[screen]) from None
        self.last_ms[screen] = round((time.monotonic() - t0) * 1000, 1)
        self.last_error[screen] = None
        self.count += 1
        return png

    async def _recycle(self, screen: str) -> None:
        """A page that failed or timed out is in an unknown state: rebuild it in the background."""
        page = self._pages.get(screen)
        if page is not None:
            page._sm_crashed = True     # _ensure_page will replace it
        self._warm(screen)

    async def _snapshot(self, screen: str, state: dict, stage: list[str] | None = None) -> bytes:
        stage = stage if stage is not None else ["start"]
        frame_key = None
        if state.get("kind") == "video":
            clip_url = urljoin(self.clip_base, state["base_url"])   # a validated /output/ path
            clip_t = float(state.get("clip_t") or 0.0)
            stage[0] = "clip frame"
            frame = await extract_clip_frame(self.scfg.ffmpeg, clip_url, clip_t,
                                             max(0.5, self.scfg.timeout_s - 0.5))
            frame_key = f"{screen}-{next(self._frame_ids)}.png"
            self._frames[frame_key] = frame
            state = {**state, "kind": "image", "clip_t": None,
                     "base_url": f"{self.origin}/__sm_snapshot/{frame_key}"}
        try:
            async with self._lock(screen):
                stage[0] = "page"
                page = await self._ensure_page(screen, state)
                stage[0] = "render"
                await page.evaluate("s => window.__smRenderArt(s)", state)
                stage[0] = "capture"
                shot = await self._cdp[screen].send("Page.captureScreenshot", {
                    "format": "png", "optimizeForSpeed": True, "fromSurface": True,
                    "captureBeyondViewport": False})
                return base64.b64decode(shot["data"])
        finally:
            if frame_key:
                self._frames.pop(frame_key, None)
