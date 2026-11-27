"""The lighting half of the Scene library (owner, 27 Nov: "the scene, effect & transition library should also allow
review of the lighting scenes as well as render scenes ... live lighting scenes parity with render scenes").

The screens' library is stream_service's (its console at /renderer/dev: a tile with a picture for every render scene,
effect and transition, rated with stars). This serves the same for the lights, from config/live/light_*.yaml, to that
console's Lighting tab:

  GET  /api/v1/lights/library                 {"rev", "items": [...], "groups": {...}, "lineups": {...}, "room_light"}
  GET  /api/v1/lights/thumb/{kind}/{name}.jpg the item's picture (tools/room/light_shots.py took it: the real look
                                              engine's output for the item, drawn by the 3D room)
  GET  /api/v1/lights/ratings                 {"ratings": {key: {"stars"?, "reject"?, "tags"?, "at"}}}
  POST /api/v1/lights/rate {"key", "stars"?, "reject"?, "tags"?}   -> {"key", "entry"}

An item is shaped as the screens' are (stream_service/library.py), so the console draws both with the same tile:
``key`` ("light/<name>", "light_effect/<name>", "light_transition/<name>"), ``kind``, ``name``, ``label``,
``description`` (what it does, in words made from its layers), ``energy``, ``facets`` (moods; plays_on: the light
groups here that can show it; sections; when: an effect's triggers), ``thumb``, and ``light``: what only a lighting
item has, among it how it sits in the layers (docs/LIGHTS.md §7.5): ``share`` (how much of the group's full light its
brightness layers leave on average), ``lands`` (the surfaces its moves land on), ``rest`` (a resting scene), and for
each group that can show it the room light it makes at full in a plain look (``lux``).

The ratings are the owner's view, kept in config/live/light_ratings.json. They don't steer the art director's draw
yet: a scene's ``tags.weight`` in light_scenes.yaml does.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional

from aiohttp import web

from . import contract, light_scenes as ls, roomlight

log = logging.getLogger("show.light_library")

SHOTS_DIR = pathlib.Path.home() / ".cache" / "sm-lightshots" / contract.DEPLOYMENT.id
RATINGS_FILE = contract.REPO_ROOT / "config" / "live" / "light_ratings.json"
KINDS = {"scene": "light", "effect": "light_effect", "transition": "light_transition"}
KEY_RE = re.compile(r"^(light|light_effect|light_transition)/[A-Za-z0-9@._#~-]{1,160}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
#: a plain look's colour by the kind of light (0..255), for the room light an item makes: as light_shots.py's
PLAIN = {"laser": (0, 255, 0), "derby": (255, 255, 255), "flash": (255, 255, 255), "movers": (38, 153, 255),
         "other": (255, 51, 115)}

MOVES = {"drift": "drift slowly", "circle": "circle", "sweep": "sweep", "aim": "all aim at one place", "fan": "fan out",
         "figure8": "trace figure eights", "ballyhoo": "sweep wide", "bounce": "hop between places",
         "tilt_wave": "tilt in a wave", "lift": "lift"}
SURFACES = {"ceiling": "on the ceiling", "floor": "on the floor", "wall-back": "on the back wall",
            "wall-front": "on the front wall"}
PULSES = {("swell", "beat"): "pulses on the beat", ("swell", "bar"): "swells once a bar", ("square", "beat"): "flashes on the beat",
          ("square", "bar"): "switches by the bar", ("sine", "bar"): "breathes over the bars", ("sine", "phrase"): "a slow wave",
          ("sine", "time"): "breathes", ("triangle", "time"): "shimmers", ("saw", "bar"): "rises through the bar"}
WHEN = {"drop": "the drop", "build": "a build", "breakdown": "a breakdown", "peak": "the peak", "section_change": "a new section",
        "track_change": "a new song", "outro": "the outro", "every_bars": "every few bars", "chorus": "a chorus",
        "final_chorus": "the last chorus", "climax": "the song's climax", "lead_in": "a song is cued", "waiting": "nothing plays",
        "lull": "a long wait", "song_end": "a song ends", "transition": "a move asks for it"}
KIND_WORDS = {"cut": "The new scene cuts in", "dark_move": "The heads move in the dark, then the new scene cuts in",
              "crossfade": "One scene fades into the next", "fade_dark": "Down to dark, then up into the new scene",
              "sweep": "The change sweeps across the room"}


def does(entry: Mapping[str, Any]) -> list[str]:
    """What a scene or an effect does, in a few words, from its layers."""
    out: list[str] = []
    for ln in entry.get("layers") or ():
        if not isinstance(ln, Mapping):
            continue
        kind = ln.get("kind", "intensity")
        if kind == "position":
            w = f"the heads {MOVES.get(ln.get('mode'), 'move')}"
            if isinstance(ln.get("on"), str):
                w += f" {SURFACES.get(ln['on'], 'on ' + ln['on'].replace('-', ' '))}"
            out.append(w)
        elif kind == "gobo":
            out.append("patterns in the beams")
        elif kind == "prism":
            out.append("split beams")
        elif kind == "shutter":
            out.append("strobes")
        elif kind in ("colour", "colour_wheel"):
            out.append("a kick of colour")
        elif kind == "macro":
            st = ln.get("set") or {}
            if any("otation" in str(k) for k in st):
                out.append("still" if all(v == 0 for v in st.values() if not isinstance(v, Mapping)) and
                           not any(isinstance(v, Mapping) for v in st.values()) else "turning")
        elif kind == "intensity":
            fx = ln.get("fx")
            if fx == "dim":
                d = ln.get("depth", 1.0)
                out.append("dark" if not isinstance(d, (int, float)) or d >= 0.99 else "a low glow")
            elif fx:
                w = PULSES.get((fx, ln.get("phase")), "the level moves")
                if isinstance(ln.get("spread"), (int, float)) and ln["spread"] >= 0.5 and fx != "dim":
                    w += ", light to light"
                out.append(w)
    hits = entry.get("hits") if isinstance(entry.get("hits"), Mapping) else None
    if hits:
        if hits.get("styles"):
            out.append("flashes on the music's hits")
        if hits.get("drop"):
            out.append("a flash on the drop")
    seen: list[str] = []
    for w in out:
        if w not in seen:
            seen.append(w)
    if not seen and entry.get("appliances"):             # no lamps' layers: it works the room's remote appliances
        return ["works the room's own effects (a glitterball, pinspots)"]
    return seen or ["steady light"]


def _sentence(words: list[str]) -> str:
    s = "; ".join(words)
    return s[:1].upper() + s[1:] + "." if s else ""


def takes(cat: Any, entry: Mapping[str, Any], group: Mapping[str, Any]) -> bool:
    """Whether a light group can show an item: it fits the group's lights, and an item written for one kind of light
    (tags.parts) goes only to a group that takes that part (scenes_for), which takes no other."""
    parts = set((entry.get("tags") or {}).get("parts") or ())
    want = set(group.get("scenes_for") or ())
    try:
        return bool(cat.fits(entry, group)) and (bool(parts & want) if (parts or want) else True)
    except Exception:
        return False


class LightLibrary:
    """The index, the pictures and the ratings. ``director_fn() -> the director component`` (its groups, its line-ups,
    the rig it last saw), or None before the brain is up: then every item is listed with no groups."""

    def __init__(self, director_fn: Callable[[], Any], shots_dir: pathlib.Path = SHOTS_DIR,
                 ratings_file: pathlib.Path = RATINGS_FILE) -> None:
        self.director_fn, self.shots_dir, self.ratings_file = director_fn, pathlib.Path(shots_dir), pathlib.Path(ratings_file)
        self._lock = threading.Lock()
        self._ratings: Optional[dict] = None

    # -- the index -------------------------------------------------------------------------------------------------
    def _thumb(self, kind: str, name: str) -> Optional[str]:
        f = self.shots_dir / kind / f"{name}.jpg"
        try:
            return f"/api/v1/lights/thumb/{kind}/{name}.jpg?v={int(f.stat().st_mtime)}"
        except OSError:
            return None

    def index(self) -> dict:
        cat = ls.shared()
        d = None
        try:
            d = self.director_fn()
        except Exception:
            log.debug("light library: no director", exc_info=True)
        groups = dict(getattr(d, "_groups", None) or {})
        lights_cfg = getattr(d, "lights_cfg", None) or {}
        gcfg = lights_cfg.get("groups") or {}
        photo = getattr(d, "_photo", None) or roomlight.load()
        rig = getattr(d, "_rig_seen", None)
        rig = rig if isinstance(rig, dict) else {}
        fixtures, types = rig.get("fixtures") or {}, rig.get("types") or {}
        size = photo["size"]
        try:
            _sp, view = d._space_now()
            sz = ((view or {}).get("room") or {}).get("size")
            if isinstance(sz, (list, tuple)) and len(sz) == 3:
                size = sz
        except Exception:
            pass
        reflect = roomlight.reflect_of((lights_cfg.get("room_light") or {}).get("reflect"), photo["reflect"])
        per_lm = roomlight.lux_per_lumen(size, reflect)

        def plain_lux(gid: str) -> float:               # the group at full, in the plain look's colour
            g, cfg = groups[gid], gcfg.get(gid) or {}
            part = next((p for p in cfg.get("scenes_for") or () if p in PLAIN), None)
            rgb = PLAIN[part] if part else PLAIN["movers"] if "pan" in (g.get("caps") or ()) else PLAIN["other"]
            lm = 0.0
            for fid in g["members"]:
                f = fixtures.get(fid) or {}
                t = types.get(f.get("type")) or {}
                lm += roomlight.lumens(photo["types"].get(t.get("file")) or photo["types"].get(f.get("type")), rgb)
            return lm * per_lm
        # the show's own groups: not "*" (the lights no group takes: house lights, the hazer)
        full = {gid: plain_lux(gid) for gid, g in groups.items() if g.get("members") and gid != ls.OTHER_GROUP}
        gests = {ls.gesture_name(n) for n in cat.transitions}
        items: list[dict] = []
        for kind, src in (("scene", cat.scenes), ("effect", cat.effects), ("transition", cat.transitions)):
            for name, e in src.items():
                if kind == "effect" and name in gests:   # a transition's own gesture: it is the transition's
                    continue
                tags = e.get("tags") if isinstance(e.get("tags"), Mapping) else {}
                gest = (e.get("gesture") or {}).get("effect") if kind == "transition" else None
                probe = e if kind != "transition" else (gest or {"layers": [], "needs": []})
                on = [gid for gid, g in groups.items() if gid in full and takes(cat, probe, g)]
                share = roomlight.scene_share(e.get("layers")) if kind == "scene" else None
                if kind == "transition":
                    words = [KIND_WORDS.get(e.get("kind"), "The scenes swap")]
                    if gest:
                        words.append("across it " + "; ".join(does(gest)))
                    desc = _sentence(words)
                elif kind == "effect":
                    when = [WHEN.get(x, x) for x in e.get("on") or ()]
                    desc = _sentence(does(e)) + (f" Fires when: {', '.join(when)}." if when else "")
                else:
                    desc = _sentence(does(e))
                light = {"needs": list(e.get("needs") or ()), "parts": list(tags.get("parts") or ()),
                         "lands": sorted(ls.lands_of(e)), "share": round(share, 2) if share is not None else None,
                         "rest": bool(tags.get("rest")), "moments": list(tags.get("moments") or ()),
                         "modes": list(tags.get("modes") or ()), "on": list(e.get("on") or ()) if kind == "effect" else [],
                         "transition_kind": e.get("kind") if kind == "transition" else None,
                         "groups": [{"id": gid, "label": groups[gid].get("label") or gid,
                                     "lux": round(full[gid] * (share if share is not None else 1.0), 1)} for gid in on]}
                items.append({
                    "key": f"{KINDS[kind]}/{name}", "kind": KINDS[kind], "name": name, "plugin": name,
                    "label": e.get("label") or name, "description": desc, "spec": None, "presets": [],
                    "energy": tags.get("energy") if isinstance(tags.get("energy"), list) else None,
                    "weight": tags.get("weight"),
                    "facets": {"moods": list(tags.get("moods") or ()), "plays_on": [g["label"] for g in light["groups"]],
                               "sections": list(tags.get("sections") or ()),
                               "style": list(tags.get("transition_style") or ()),
                               "when": [WHEN.get(x, x) for x in light["on"]],
                               "role": ["resting"] if light["rest"] else ["waiting"] if light["moments"] else []},
                    "thumb": self._thumb(kind, name), "preview": None, "load": "ok", "status": "accepted",
                    "runtime_errors": 0, "added_at": None, "light": light})
        lus = lights_cfg.get("lineups") or {}
        return {"rev": cat.revision, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": items,
                "groups": {gid: {"label": g.get("label") or gid, "members": len(g.get("members") or ()),
                                 "reads_under_lux": (gcfg.get(gid) or {}).get("reads_under_lux"),
                                 "lux": round(full.get(gid, 0.0), 1)} for gid, g in groups.items() if gid in full},
                "lineups": {n: {"label": v["label"], "plays": v["plays"], "features": v.get("features") or []}
                            for n, v in lus.items()},
                "room_light": dict(getattr(d, "_room_light_view", None) or {}),
                "problems": list(cat.problems)[:20]}

    # -- ratings ---------------------------------------------------------------------------------------------------
    def ratings(self) -> dict:
        with self._lock:
            if self._ratings is None:
                try:
                    raw = json.loads(self.ratings_file.read_text(encoding="utf-8"))
                    self._ratings = raw if isinstance(raw, dict) else {}
                except FileNotFoundError:
                    self._ratings = {}
                except Exception:
                    log.exception("light_ratings.json unreadable: starting empty (the file is left as it is)")
                    self._ratings = {}
            return dict(self._ratings)

    def rate(self, body: Any) -> dict:
        if not isinstance(body, dict) or not isinstance(body.get("key"), str) or not KEY_RE.match(body["key"]):
            raise ValueError("key must be a lighting item's key like 'light/<name>'")
        if set(body) - {"key", "stars", "reject", "tags", "looks"}:
            raise ValueError("only stars, reject and tags can be set")
        self.ratings()
        with self._lock:
            e = dict(self._ratings.get(body["key"]) or {})
            if "stars" in body:
                s = body["stars"]
                if s in (0, None):
                    e.pop("stars", None)
                elif isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5:
                    e["stars"] = s
                else:
                    raise ValueError("stars is 0 to 5")
            if "reject" in body:
                if body["reject"]:
                    e["reject"] = True
                else:
                    e.pop("reject", None)
            if "tags" in body:
                t = body["tags"]
                if not isinstance(t, list) or len(t) > 20 or not all(isinstance(x, str) and TAG_RE.match(x) for x in t):
                    raise ValueError("tags is a list of up to 20 short words")
                if t:
                    e["tags"] = t
                else:
                    e.pop("tags", None)
            if [k for k in e if k != "at"]:
                e["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._ratings[body["key"]] = e
            else:
                self._ratings.pop(body["key"], None)
            tmp = self.ratings_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._ratings, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.ratings_file)
            return {"key": body["key"], "entry": self._ratings.get(body["key"])}

    # -- HTTP ------------------------------------------------------------------------------------------------------
    def routes(self, app: Any) -> None:
        r = app.router
        r.add_get("/api/v1/lights/library", self._h_library)
        r.add_get("/api/v1/lights/thumb/{kind}/{name}.jpg", self._h_thumb)
        r.add_get("/api/v1/lights/ratings", self._h_ratings)
        r.add_post("/api/v1/lights/rate", self._h_rate)

    async def _h_library(self, _req: web.Request) -> web.Response:
        try:
            return web.json_response(self.index(), headers={"Cache-Control": "no-store"})
        except Exception as e:
            log.exception("light library: index failed")
            return web.json_response({"error": "index_failed", "message": f"{type(e).__name__}: {e}"}, status=500)

    async def _h_thumb(self, req: web.Request) -> web.StreamResponse:
        kind, name = req.match_info["kind"], req.match_info["name"]
        if kind not in KINDS or not re.match(r"^[A-Za-z0-9@._#~-]{1,160}$", name):
            raise web.HTTPNotFound()
        f = self.shots_dir / kind / f"{name}.jpg"
        if not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Cache-Control": "max-age=60"})

    async def _h_ratings(self, _req: web.Request) -> web.Response:
        return web.json_response({"ratings": self.ratings()}, headers={"Cache-Control": "no-store"})

    async def _h_rate(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
            return web.json_response(self.rate(body))
        except ValueError as e:
            return web.json_response({"error": "bad_request", "message": str(e)}, status=400)
        except Exception as e:
            log.exception("light library: rate failed")
            return web.json_response({"error": "failed", "message": f"{type(e).__name__}: {e}"}, status=500)
