"""Live art: a screen's current art for the renderer's `live_art` scene, and the scene pool a mode's
`scene_tags` pick (owner, 14 Oct: Ambient shows effects ON the art, never other scenes).

  GET /api/v1/screens/{id}/art        Open (art.png is an alias). The screen's art as its TV art page shows it
                                      now, WITH the light mask the TV draws (brightness + warm tint; no
                                      overlays), frozen at its Normal value while a live mode holds the sensor
                                      mask (owner, 15 Oct: the renderer never masks, and its effects go on top
                                      of this unmasked), from the live art feed (live_art_feed.py), which
                                      follows the art every second: a still is captured once per publish in the
                                      feed's own niced headless Chrome, a clip plays the TV's slow-motion loop.
                                      200 image/jpeg; ETag "<frame id>" (changes with the pixels and the mask);
                                      X-Art-Kind still|video; X-Art-Next-S (video: seconds to the next frame);
                                      no-store. 304 on a matching If-None-Match (same X-Art-* headers). 404 JSON
                                      unknown_screen | no_art; 503 warming (+ Retry-After: 1) until the first
                                      frame exists (and while room is still starting). A new art's frame
                                      replaces the previous one when it is ready.

The renderer's live_art source (stream_service/plugins/sources/live_art.py) polls this every second.

**Scene tags.** A mode's live block may carry `scene_tags: ["ambient"]`: the director's pool becomes exactly the
scene presets whose `tags.modes` lists one of them (resolved here against the renderer's catalog, because the
renderer's own tag filter lets presets without the key through). Every session gets the display's default auto
block (publish-destinations `live.auto`); a mode's `auto: true | false | {...}` overlays or disables it.

**Look carry-over** (`carried_look`): when room has to create a new session for a screen that was live a moment
ago (a page reload, a fallback, a profile change, a restart's adoption gap), the old session's look (director,
direction, palette, hand-placed effects, and the scene it had reached) is carried into the new one.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .live_art_feed import LiveArtFeed, feed_off_reason
from .util import spawn

if TYPE_CHECKING:
    from .service import RoomService

log = logging.getLogger("room_service.live_art")

DEFAULT_AUTO = {"enabled": True, "fx": {"enabled": True, "max": 2, "pool": "all"}}


def preset_tags(p: dict) -> set[tuple[str, str]]:
    """A scene preset's tag words as (facet, word): every facet whose value is a word or a list of words (modes,
    including the owner's Library tags; moods; family; technique; dims). Numbers (energy, weight) are not tags."""
    out: set[tuple[str, str]] = set()
    for facet, v in ((p or {}).get("tags") or {}).items():
        for w in [v] if isinstance(v, str) else v if isinstance(v, list) else []:
            if isinstance(w, str) and w:
                out.add((facet, w))
    return out


def scene_tag_list(catalog: dict) -> list[dict]:
    """Every tag word on the scene presets: [{tag, facet, count}], the renderer's catalog.scene_tags when it has one."""
    given = (catalog or {}).get("scene_tags")
    if isinstance(given, list) and given:
        return [t for t in given if isinstance(t, dict) and isinstance(t.get("tag"), str)]
    counts: dict[tuple[str, str], int] = {}
    for p in (((catalog or {}).get("presets") or {}).get("scenes") or {}).values():
        for ft in preset_tags(p):
            counts[ft] = counts.get(ft, 0) + 1
    return [{"tag": w, "facet": f, "count": n} for (f, w), n in sorted(counts.items(), key=lambda x: (x[0][0], x[0][1]))]


def tag_matches(have: set[tuple[str, str]], tag: str) -> bool:
    """`word` matches that word in any facet; `facet:word` only in that facet (e.g. "modes:ambient")."""
    if ":" in tag:
        facet, word = tag.split(":", 1)
        return (facet, word) in have
    return any(w == tag for _, w in have)


def tagged_scenes(catalog: dict, tags: list[str]) -> list[str]:
    """Scene presets matching any of `tags` (one generic rule, owner 16 Oct: a mode HAPPENS to use piano-tagged
    scenes; that's config). A word matches any facet; "facet:word" one facet, so a closed set stays closed:
    Ambient = ["modes:ambient"] (just live-art; "ambient" alone would also match the genre/mood word)."""
    scenes = ((catalog or {}).get("presets") or {}).get("scenes") or {}
    out = []
    for name, p in scenes.items():
        have = preset_tags(p)
        if have and any(tag_matches(have, t) for t in tags):
            out.append(name)
    return sorted(out)


async def auto_block(live: dict, default: dict | None, stream) -> dict | None:
    """The `auto` block for a session (or a PATCH) from the mode's live value: None = no auto block.

    `default` is the display's live.auto (publish-destinations): every session gets it unless the mode says
    otherwise. The mode's `auto` may be true/false or an auto block overlaid on it; `scene_tags` pins the pool
    (and implies auto on)."""
    a, tags = live.get("auto"), live.get("scene_tags")
    if a is False:
        return {"enabled": False}
    if a is None and not tags:
        if not default:
            return None
        out = copy.deepcopy(default)
        out.setdefault("pool", "all")    # explicit: a PATCH merges over the old block, so a closed pool would stay
        return out
    out = copy.deepcopy(default or DEFAULT_AUTO)
    if isinstance(a, dict):
        fx = {**(out.get("fx") or {}), **(a.get("fx") or {})}
        out.update(copy.deepcopy(a))
        if fx:
            out["fx"] = fx
    out["enabled"] = True
    if tags:
        names = tagged_scenes(await stream.catalog(), list(tags))
        if not names:                            # never an empty pool: the mode's own scene
            scene = live.get("scene")
            names = [scene if isinstance(scene, str) else (scene or {}).get("preset") or "fractal-zoom"]
            log.warning("scene_tags %s match no scene preset; pool = %s", tags, names)
        out["pool"] = names
    else:
        out.setdefault("pool", "all")    # leaving a tagged mode (Ambient, Piano) must open the pool again
    return out


# The renderer's neutral direction (live_api.DIRECTION defaults): sent when a mode without one follows a mode with one,
# because a PATCHed direction merges over the old (Party -> Ambient must not keep Party's energy and moods).
NEUTRAL_DIRECTION = {"energy": 0.5, "pace": 0.5, "intensity": 1.0, "change_rate": 0.5, "moods": [], "genre": None,
                     "transition_style": None, "palette_family": None,
                     "fx": {"density": 0.5, "rate": 0.5, "stages": None, "moods": None}}

AUTO_KEYS = ("enabled", "pool", "transitions", "dwell_s", "snap", "follow_dmx", "avoid_repeat", "seed", "fx", "music")
AUTO_FX_KEYS = ("enabled", "pool", "max")
AUTO_MUSIC_KEYS = ("enabled", "weight", "activity")
LOOK_KEYS = ("direction", "palette", "palette_transition_s", "beat_follow")


def carried_look(sess: dict) -> dict:
    """What to carry from a renderer session (GET /api/sessions/{sid}) into its successor:
    {"look": create-body look fields, "auto": its director block, "scene": the preset it had reached,
     "fx": the hand-placed FX stack entries}. Runtime-only keys are dropped (the renderer rejects them)."""
    look = {k: copy.deepcopy(sess[k]) for k in LOOK_KEYS if sess.get(k) is not None}
    a = sess.get("auto")
    auto = None
    if isinstance(a, dict):
        auto = {k: copy.deepcopy(a[k]) for k in AUTO_KEYS if k in a}
        if isinstance(auto.get("fx"), dict):
            auto["fx"] = {k: auto["fx"][k] for k in AUTO_FX_KEYS if k in auto["fx"]}
        if isinstance(auto.get("music"), dict):
            auto["music"] = {k: auto["music"][k] for k in AUTO_MUSIC_KEYS if k in auto["music"]}
    stack = ((sess.get("fx") or {}).get("stack")) or []
    fx = [{k: e[k] for k in ("preset", "effect", "params", "level", "bypass") if e.get(k) is not None}
          for e in stack if isinstance(e, dict) and e.get("origin") != "auto"]
    scene = sess.get("current_scene") if isinstance(sess.get("current_scene"), str) else None
    return {"look": look, "auto": auto, "scene": scene, "fx": [e for e in fx if e.get("preset") or e.get("effect")]}


def _etag_matches(header: str | None, etag: str) -> bool:
    return bool(header) and any(t.strip().removeprefix("W/") == etag for t in header.split(","))


def register(app: web.Application, svc: "RoomService", feed: LiveArtFeed | None = None) -> None:
    """The art route, and the feed's lifecycle on the app's startup/cleanup signals. `feed`: tests pass one
    built with fakes; otherwise one starts once RoomService.start() has built the screens (it runs after this
    module's startup hook), unless snapshots are off or a test snapshotter is installed."""
    holder: dict[str, Any] = {}

    def err(status: int, code: str, detail: str = "", retry: float | None = None) -> web.Response:
        headers = {"Retry-After": str(int(retry))} if retry else None
        return web.json_response({"error": code, "detail": detail}, status=status, headers=headers)

    async def art(request: web.Request) -> web.StreamResponse:
        sid = request.match_info["id"]
        screens = getattr(svc, "screens", None)
        if screens is None:                      # just after a start: RoomService.start() has not built them yet
            return err(503, "warming", "room is starting", retry=1)
        if screens.get(sid) is None:
            return err(404, "unknown_screen", sid)
        f: LiveArtFeed | None = holder.get("feed")
        if f is None:
            if holder.get("off"):
                return err(404, "no_art", f"live art is off: {holder['off']}")
            return err(503, "warming", "the live art feed is starting", retry=1)
        s = f.lookup(sid)
        if s.status in ("off", "no_art"):
            return err(404, "no_art", s.detail)
        if s.status != "ok":
            return err(503, "warming", s.detail, retry=1)
        headers = {"ETag": f'"{s.etag}"', "X-Art-Kind": s.kind, "Cache-Control": "no-store"}
        if s.next_s is not None:
            headers["X-Art-Next-S"] = f"{s.next_s:.3f}"
        if _etag_matches(request.headers.get("If-None-Match"), headers["ETag"]):
            return web.Response(status=304, headers=headers)
        try:
            body = await f.read(sid, s)
        except OSError as e:                     # a clip's frame files replaced under us: the next poll gets it
            return err(503, "warming", f"frame unreadable: {e.__class__.__name__}", retry=1)
        return web.Response(body=body, content_type="image/jpeg", headers=headers)

    async def on_startup(app_: web.Application) -> None:
        async def boot() -> None:
            if feed is not None:
                await feed.start()
                holder["feed"] = feed
                return
            for _ in range(1200):                # RoomService.start() builds screens + http after this hook
                if getattr(svc, "screens", None) is not None and getattr(svc, "http", None) is not None:
                    break
                await asyncio.sleep(0.05)
            else:
                holder["off"] = "room screens never came up"
                log.warning("live art feed: svc.screens never appeared; feed off")
                return
            why = feed_off_reason(svc)
            if why:
                holder["off"] = why
                log.info("live art feed off: %s", why)
                return
            f = LiveArtFeed(svc)
            await f.start()
            holder["feed"] = f

        holder["boot"] = spawn(boot(), "live-art-feed-boot")

    async def on_cleanup(app_: web.Application) -> None:
        t = holder.pop("boot", None)
        if t is not None and not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        f = holder.pop("feed", None)
        if f is not None:
            try:
                await asyncio.wait_for(f.close(), 10.0)
            except Exception as e:
                log.warning("live art feed did not close cleanly: %s", e)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/api/v1/screens/{id}/art", art)
    app.router.add_get("/api/v1/screens/{id}/art.png", art)
