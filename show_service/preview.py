"""preview: the rig as it WOULD look, for the Light setup page's 3D view, never sent to a light (owner, 27 Nov: "the
3D view in light setup isn't the same as a real time view. it should allow user easy to set theme / scene etc.").

A preview is this deployment's own look engine (looks.py), built from its own files (fixtures, show.yaml, the room
model, config/live/light_*.yaml), run on its own steady 120 bpm clock, one per viewer. What it shows (``what``):

  plain            every light steady at full in a plain light (white; a laser green, a derby white), the moving heads
                   at rest: for aiming a light and seeing where it lands
  colour:<hex>     every light steady in that colour
  scene:<name>     a light scene from the catalogue on every light group that can play it, in the plain light
                   (``colour`` with it: in that colour); the other groups glow low
  mode             the rig as a room mode drives it (owner, 27 Nov: "It's ... confusing if it is meant to simulate a
                   mode, but it doesn't do that"): ``vibe`` is the feel the mode sets (its show.main), ``@current`` the
                   room's feel now (a mode that leaves the feel alone), empty: no feel. No feel: the DMX lights are
                   held as the real output holds them (``idle_without_vibe``: each at its house look, else dark). A
                   feel: each light group plays from its pool for the mode's ``tags`` (director.light_scenes
                   scene_tags; light_scenes.pool_for, as the director draws), a new scene each phrase, in the feel's
                   palettes, at its level and motion for ``energy``; never a strobe

and the music it plays to: ``energy`` (0..1: how hard it drives, which is how far and fast the heads move, how dense
the accents are, how bright the look is) and ``bpm`` (its tempo; the beat is steady).

Its frames are packed as the Art-Net output packs them and offered in the virtual GigBAR node's own shapes, so the
3D room (src/pages/Room3D) reads a preview exactly as it reads the room:

  GET /api/v1/rig/preview/status          {"node": {"universe": 1}, "artnet": {"pps": 25, "age_s": 0, ...}}
  GET /api/v1/rig/preview/dmx?what=...&colour=#rrggbb   Server-Sent Events, data: {"d": <512 bytes, base64>}

Nothing here touches the room: no output, no director, no state. The engine is built when a viewer connects (a few
hundred ms) and dropped when it leaves; at most MAX_LIVE run at once.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Optional

from aiohttp import web

from . import artnet_lan, clock as clockmod, config as cfgmod, contract, fixtures, light_scenes as ls

log = logging.getLogger(__name__)

FPS = 25.0
MAX_LIVE = 3
BPM = 120.0
ENERGY = 0.6
PLAIN = {"laser": (0.0, 1.0, 0.0), "derby": (1.0, 1.0, 1.0), "flash": (1.0, 1.0, 1.0), "other": (1.0, 0.92, 0.82)}
STEADY = {"label": "steady", "needs": [], "layers": [], "hits": None, "caps": None, "tags": {}, "appliances": None}
LOW = {**STEADY, "layers": [{"id": "low", "fx": "dim", "phase": "time", "period_s": 1, "depth": 0.85, "needs": "none"}]}
DARK = {**STEADY, "layers": [{"id": "dark", "fx": "dim", "phase": "time", "period_s": 1, "depth": 1.0, "needs": "none"}]}
_live = 0


class _Clock:
    """A steady beat from when the preview started: the look engine's clock (snapshot and next_beat)."""

    def __init__(self, t0: float, bpm: float = BPM) -> None:
        self.body = {"bpm": bpm, "anchor_ms": t0 * 1000.0, "beat_at_anchor": 0, "beats_per_bar": 4,
                     "bar_anchor_beat": 0, "phrase_bars": 8, "conf": 0.9, "bar_conf": 0.9, "phrase_conf": 0.9,
                     "state": "locked", "gate": "beat", "epoch": 1, "stability": 0.5, "source": "preview",
                     "t_ms": t0 * 1000.0}

    def snapshot(self) -> dict:
        return self.body

    def next_beat(self, t_s: float, lead_s: float = 0.0, *, gate: str = "beat") -> Optional[float]:
        r = clockmod.next_beat(self.body, t_s, lead_s)        # type: ignore[arg-type]
        return None if r is None else r[1]


def _features(t_s: float, energy: float = ENERGY) -> dict:
    return {"t_ms": t_s * 1000.0, "stream": "preview",
            "bands": {n: {"level": 0.3, "hits": 0, "presence": 0.3} for n in contract.BAND_NAMES},
            "onset": 0.0, "lufs_m": -18.0, "lufs_s": -18.0, "peak_dbfs": -6.0, "rms_dbfs": -18.0,
            "brightness": 0.5, "noisiness": 0.1, "gate": 1.0, "energy": energy, "punch": energy, "drive": energy,
            "spectrum32": [0.0] * 32}


def _hex(s: Optional[str]) -> Optional[tuple[float, float, float]]:
    s = (s or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)
    except ValueError:
        return None


class Preview:
    """One viewer's preview: the engine, what it shows, and the 512 slots it would send."""

    def __init__(self, what: str, colour: Optional[tuple[float, float, float]], energy: float = ENERGY,
                 bpm: float = BPM, sets: Optional[dict[str, float]] = None, vibe: Optional[str] = None,
                 tags: tuple[str, ...] = ()) -> None:
        self.energy, self.bpm = max(0.0, min(1.0, energy)), max(40.0, min(220.0, bpm))
        from .looks import LookEngine
        from . import space as sp
        self.view = fixtures.load(contract.FIXTURES_FILE)
        show = cfgmod.read_show(contract.SHOW_FILE)
        self.cat = ls.Catalogue()
        self.cat.poll()
        self.cat.scenes = {**self.cat.scenes, "preview-steady": STEADY, "preview-low": LOW, "preview-dark": DARK}
        try:
            space = sp.shared().view()
        except Exception:                                 # noqa: BLE001 - no room model: the engine does without
            space = None
        probs: list[str] = []
        self.lights = ls.parse_lights(show.get("lights"), probs)
        self.groups = ls.group_members(self.lights["groups"], self.view, lamps=show.get("lamps"))
        self.t0 = self.now = time.monotonic()
        self.vibe, self.tags, self.phrase = vibe, tuple(tags), -1
        self.vs: Optional[dict] = None                    # the mode's feel's settings (what=mode with a feel)
        self.pal_cols: list[list[tuple[float, float, float]]] = []
        self.pal: dict = {}
        self.look: dict = {}
        self.engine = LookEngine(palette_fn=lambda t: self.pal, look_fn=lambda: self.look, clock=_Clock(self.t0, self.bpm),
                                 features_fn=lambda: _features(self.now, self.energy), mono=lambda: self.now, light_scenes=self.cat,
                                 space_mod=sp if space is not None else False)
        self.engine.configure(show["looks"], self.view, lamps=show.get("lamps"), lights=show.get("lights"),
                              space=space)
        fx, types = self.view.get("fixtures") or {}, self.view.get("types") or {}
        outs = sorted({f.get("output") for f in fx.values() if isinstance(f.get("address"), int)} - {None})
        if not outs:
            raise ValueError("no DMX lights in this deployment")
        self.output = outs[0]
        base = bytearray(512)
        self.spans: dict[str, tuple[int, int]] = {}
        self.hazers: set[str] = set()
        for fid, f in fx.items():                         # the universe as artnet_lan lays it out
            t = types.get(f.get("type")) or {}
            fp, a = int(t.get("footprint") or 0), f.get("address")
            if f.get("output") != self.output or fp <= 0 or not isinstance(a, int) or a < 1 or a + fp - 1 > 512:
                continue
            names: dict[str, int] = {}
            for ch in t.get("channels") or []:
                o = ch.get("offset")
                if isinstance(o, int) and 0 <= o < fp:
                    base[a - 1 + o] = max(0, min(255, int(ch.get("default", 0) or 0)))
                    names.setdefault(str(ch.get("name")), o)
            for name, v in (f.get("pin") or {}).items():
                if name in names:
                    base[a - 1 + names[name]] = max(0, min(255, int(v)))
            self.spans[str(fid)] = (a - 1, fp)
            if "haze" in (f.get("tags") or ()):
                self.hazers.add(str(fid))                 # a preview never runs the hazer
        self.base = bytes(base)
        # the lights a mode sets (owner, 27 Nov: "why are house lights up in all of them? ... surely default in a live
        # mode is that they would be off"): the page sends the chosen mode's levels; each such light shows that level
        # on its dimmer (its first channel named like one, else a one-channel light's only channel), whatever the look
        self.sets: dict[int, int] = {}
        for fid, lvl in (sets or {}).items():
            f, sp_ = fx.get(fid) or {}, self.spans.get(str(fid))
            if not sp_:
                continue
            t = types.get(f.get("type")) or {}
            off = next((ch.get("offset") for ch in t.get("channels") or []
                        if isinstance(ch.get("offset"), int) and str(ch.get("name", "")).lower() in ("dimmer", "intensity", "master dimmer")),
                       0 if sp_[1] == 1 else None)
            if off is not None:
                self.sets[sp_[0] + off] = max(0, min(255, round(255 * lvl)))
        self.what = what
        if what == "mode" and vibe:
            from . import palette as palmod, vibes as vibesmod
            vb = vibesmod.Vibes()
            vb.poll(force=True)
            self.vs = dict(vb.get(vibe) or vb.effective({"vibe": vibe, "listening": False})[0])
            cat = palmod.Catalog()
            cat.poll()
            names = [n for n in self.vs.get("palettes") or () if cat.has(n)] or cat.names()[:1]
            self.pal_cols = [cat.colours(n, 5) for n in names]
        self._show(what, colour)

    def _kind(self, gid: str) -> str:
        cfg = self.lights["groups"].get(gid) or {}
        return next((p for p in cfg.get("scenes_for") or () if p in PLAIN), "other")

    def _phrase(self) -> int:
        """Which phrase (8 bars of 4) of the preview's steady clock it is: a mode's preview changes scene on each."""
        return int((self.now - self.t0) * self.bpm / 60.0 // 32)

    @staticmethod
    def _span(v: Any, e: float, default: float) -> float:
        """A feel's [lo, hi] (or one number) at energy ``e``."""
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            return float(v[0]) + (float(v[1]) - float(v[0])) * e
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default

    def _show_mode(self) -> None:
        """what=mode: the rig as the mode drives it (the module docstring)."""
        self.phrase = self._phrase()
        at = self.now * 1000.0
        if self.vs is None:                               # no feel: the engine's own no-vibe hold (house look or dark)
            self.pal, self.look = None, {}                # type: ignore[assignment]
            return
        vs, e, k = self.vs, self.energy, self.phrase
        cols = self.pal_cols[k % len(self.pal_cols)] if self.pal_cols else [PLAIN["other"]]
        pal: dict = {}
        progs = {}
        for i, (gid, g) in enumerate(self.groups.items()):
            c = cols[(i + k) % len(cols)]
            for fid in g["members"]:
                pal[fid] = c
            pool = [n for n in self.cat.pool_for(g, self.tags or None) if not n.startswith("preview-")]
            sc = pool[(k + i) % len(pool)] if pool and g["members"] else "preview-low"
            progs[gid] = {"group": gid, "label": g.get("label") or gid, "members": list(g["members"]),
                          "caps": list(g.get("caps") or ()), "scene": sc, "effects": [],
                          "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": at}, "at_ms": at,
                          "since_ms": at, "why": "a preview of a mode",
                          "flow": {"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "preview", "at_ms": at,
                                   "screens": {}}}
        pal["*"] = cols[0]
        self.pal = pal
        motion = self._span(vs.get("motion"), e, 0.4)
        self.look = {"t_ms": at, "seq": 1 + k, "look_id": f"preview-{k}", "vibe": self.vibe, "mode": None, "palette": {},
                     "level": self._span(vs.get("level"), e, 0.8), "floor": self._span(vs.get("floor"), 0.0, 0.0),
                     "house": False, "low": False, "key_cct": float(vs.get("key_cct") or 4000.0),
                     "chroma_max": float(vs.get("chroma_max") or 1.0), "lamp_chroma_max": 1.0,
                     "saturation": self._span(vs.get("saturation"), e, 1.0), "contrast": self._span(vs.get("contrast"), e, 0.5),
                     "motion": {"speed": motion, "amount": motion, "idle": float(vs.get("idle_motion") or 0.2)},
                     "accents": {"style": vs.get("accents") or "beat", "density": e, "subdivision": 2 if e > 0.85 else 1,
                                 "strobe": False, "beat_lock": True},
                     "spread": 0.0, "change_rate": 0.5, "transition": {"style": "cut", "beats": None, "seconds": None},
                     "lamps": {}, "screens": {}, "light_programs": progs,
                     "flow": {"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "preview", "at_ms": at, "screens": {}}}

    def _show(self, what: str, colour: Optional[tuple[float, float, float]]) -> None:
        if what == "mode":
            self._show_mode()
            return
        scene = what[len("scene:"):] if what.startswith("scene:") else None
        if scene is not None and scene not in self.cat.scenes:
            raise ValueError(f"There's no light scene {scene!r}.")
        pal: dict = {}
        for gid, g in self.groups.items():
            c = colour if colour is not None and self._kind(gid) not in ("laser",) else PLAIN[self._kind(gid)]
            if colour is not None and self._kind(gid) == "laser":
                c = colour                                # the laser takes the diode nearest the colour
            for fid in g["members"]:
                pal[fid] = c
        pal["*"] = colour or PLAIN["other"]
        self.pal = pal
        at = self.now * 1000.0
        progs = {}
        for gid, g in self.groups.items():
            if scene is None:
                # plain or a colour: steady at full, but the strobe zones only glow (a preview never strobes anyone)
                sc = "preview-low" if self._kind(gid) == "flash" else "preview-steady"
            else:
                entry = self.cat.scenes[scene]
                parts = set((entry.get("tags") or {}).get("parts") or ())
                want = set((self.lights["groups"].get(gid) or {}).get("scenes_for") or ())
                takes = bool(self.cat.fits(entry, g)) and (bool(parts & want) if (parts or want) else True)
                sc = scene if takes and g["members"] else "preview-low"
            progs[gid] = {"group": gid, "label": g.get("label") or gid, "members": list(g["members"]),
                          "caps": list(g.get("caps") or ()), "scene": sc, "effects": [],
                          "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": at}, "at_ms": at,
                          "since_ms": at, "why": "a preview",
                          "flow": {"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "preview", "at_ms": at,
                                   "screens": {}}}
        e = self.energy
        self.look = {"t_ms": at, "seq": 1, "look_id": "preview", "vibe": "preview", "mode": None, "palette": {},
                     "level": 0.55 + 0.45 * e if scene is not None else 1.0, "floor": 0.0, "house": False, "low": False, "key_cct": 4000.0, "chroma_max": 1.0,
                     "lamp_chroma_max": 1.0, "saturation": 1.0, "contrast": 0.5,
                     "motion": {"speed": e, "amount": 0.25 + 0.65 * e, "idle": 0.4},
                     "accents": {"style": "beat", "density": e, "subdivision": 2 if e > 0.85 else 1, "strobe": False,
                                 "beat_lock": True},
                     "spread": 0.0, "change_rate": 0.5, "transition": {"style": "cut", "beats": None, "seconds": None},
                     "lamps": {}, "screens": {}, "light_programs": progs,
                     "flow": {"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "preview", "at_ms": at, "screens": {}}}

    def frame(self) -> bytes:
        """The 512 slots now."""
        self.now = time.monotonic()
        if self.what == "mode" and self.vs is not None and self._phrase() != self.phrase:
            self._show_mode()                             # a new phrase: the next scenes and colours
        fr = self.engine.evaluate(self.output, self.now, artnet_lan.CAPS)
        buf = bytearray(self.base)
        for fid, f in (fr.get("fixtures") or {}).items():
            sp_ = self.spans.get(fid)
            d = f.get("dmx")
            if sp_ and d and fid not in self.hazers:
                buf[sp_[0]:sp_[0] + min(sp_[1], len(d))] = d[:sp_[1]]
        for a, v in self.sets.items():
            buf[a] = v
        return bytes(buf)


def _current_vibe(brain: Any) -> Optional[str]:
    """The room's feel now (the director's target vibe), None without one or without a director."""
    try:
        from .api import DIRECTOR, _components
        t = getattr(_components(brain).get(DIRECTOR), "target", None)
        v = t.get("vibe") if isinstance(t, dict) else None
        return v if isinstance(v, str) and v else None
    except Exception:                                     # noqa: BLE001 - no brain (a test): no feel
        return None


def _cors(resp: web.StreamResponse) -> None:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"


async def status(_request: web.Request, _brain: Any = None) -> web.Response:
    """GET /api/v1/rig/preview/status: the virtual node's status shape, always fresh."""
    r = web.json_response({"node": {"universe": 1, "udp": "preview"},
                           "artnet": {"pps": FPS, "age_s": 0, "merging": False, "length": 512}})
    _cors(r)
    return r


async def dmx(request: web.Request, _brain: Any = None) -> web.StreamResponse:
    """GET /api/v1/rig/preview/dmx?what=plain|colour|scene:<name>|mode&colour=#rrggbb&energy=0..1&bpm=n
    &set=<id>:<0..1>,...&vibe=<name>|@current|&tags=<tag>,...: the preview's frames, as SSE. ``set``: lights a mode
    sets (house lights), held at those levels; ``vibe`` and ``tags``: a mode's feel and light scene tags (what=mode)."""
    global _live
    what = request.query.get("what") or "plain"
    colour = _hex(request.query.get("colour"))
    try:
        energy = float(request.query.get("energy", ENERGY))
        bpm = float(request.query.get("bpm", BPM))
    except ValueError:
        raise ValueError("energy is 0..1 and bpm a tempo.") from None
    sets: dict[str, float] = {}
    for part in (request.query.get("set") or "").split(","):
        fid, _, v = part.rpartition(":")
        try:
            if fid:
                sets[fid] = max(0.0, min(1.0, float(v)))
        except ValueError:
            raise ValueError("set is <light id>:<level 0..1>, comma separated.") from None
    if what == "colour" and colour is None:
        raise ValueError("A colour preview needs colour=#rrggbb.")
    if what not in ("plain", "colour", "mode") and not what.startswith("scene:"):
        raise ValueError("what is plain, colour, scene:<name> or mode.")
    vibe: Optional[str] = (request.query.get("vibe") or "").strip() or None
    if vibe == "@current":                                # a mode that leaves the feel alone: the room's feel now
        vibe = _current_vibe(_brain)
    tags = tuple(t.strip() for t in (request.query.get("tags") or "").split(",") if t.strip())
    if _live >= MAX_LIVE:
        r = web.json_response({"error": "busy", "detail": "Too many previews at once: close one."}, status=503)
        _cors(r)
        return r
    loop = asyncio.get_running_loop()
    _live += 1
    try:
        pv = await loop.run_in_executor(None, lambda: Preview(what, colour, energy, bpm, sets, vibe, tags))
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        _cors(resp)
        await resp.prepare(request)
        while True:
            d = await loop.run_in_executor(None, pv.frame)
            await resp.write(b"data: " + json.dumps({"d": base64.b64encode(d).decode()}).encode() + b"\n\n")
            await asyncio.sleep(1.0 / FPS)
    finally:                                              # the viewer went away (or the engine couldn't be built)
        _live -= 1
