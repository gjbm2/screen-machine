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
import copy
import json
import logging
import random
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
        # the room's PLANNED lights (owner, 27 Nov: "treat the studio like a studio, test layouts"): placed in the room
        # editor, not registered; they join the preview as lights of their own on a wire of their own ("planned"),
        # so the scenes run them too and the 3D room draws them, and nothing is ever sent to a light
        self.planned: dict[str, tuple[int, int]] = {}         # id -> (first slot on the planned wire, footprint)
        self._add_planned(space)
        self.lights = ls.parse_lights(show.get("lights"), probs)
        self.groups = ls.group_members(self.lights["groups"], self.view, lamps=show.get("lamps"))
        self.t0 = self.now = time.monotonic()
        self.vibe, self.tags, self.phrase = vibe, tuple(tags), -1
        self._last_scene: dict[str, str] = {}
        self._lineup: Optional[str] = None
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
        # The phrase's line-up, as the director draws one at a change (light_scenes.pick_lineup at the music's drive):
        # the kinds of light that play; the others rest (their rest scenes: the strobe zones mostly dark).
        lus = self.lights.get("lineups") or {}
        plays: Optional[set] = None
        if lus:
            name = ls.pick_lineup(lus, None, e, random.Random(f"{self.vibe}|lineup|{k}"), avoid=self._lineup)
            self._lineup = name
            plays = set(lus[name]["plays"]) if name else None
        for i, (gid, g) in enumerate(self.groups.items()):
            c = cols[(i + k) % len(cols)]
            for fid in g["members"]:
                pal[fid] = c
            # As the director draws them (light_scenes.pick_for: the mode's pool, weighted by how each fits the energy),
            # one draw a phrase, so the preview shows what the show would, not every scene in the pool in turn (owner,
            # 27 Nov: the room "flickered" every few seconds: a strobe run the show would rarely choose here).
            sc = "preview-low"
            if g["members"]:
                rng = random.Random(f"{self.vibe}|{gid}|{k}")
                got = self.cat.pick_for(g, {"energy": e}, None, rng, avoid=self._last_scene.get(gid),
                                        tags=self.tags or None,
                                        rest=None if plays is None else gid not in plays,
                                        alone=None if plays is None else plays == {gid},
                                        prefer={n: 0.0 for n in ("preview-steady", "preview-low", "preview-dark")})
                if got and not got[0].startswith("preview-"):
                    sc = got[0]
            self._last_scene[gid] = sc
            progs[gid] = {"group": gid, "label": g.get("label") or gid, "members": list(g["members"]),
                          "caps": list(g.get("caps") or ()), "scene": sc, "effects": [],
                          "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": at}, "at_ms": at,
                          "since_ms": at, "why": "a preview of a mode",
                          "flow": {"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "preview", "at_ms": at,
                                   "screens": {}}}
        pal["*"] = cols[0]
        self.pal = pal
        self._pick_appliances()
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

    # The remote-worked things when the show runs them (owner, 27 Nov: "seems like pinspots always on during karaoke?
    # ... shouldn't it be scene dependent?"): each phrase, one of the catalogue's scenes for them (an ``appliances``
    # block: pan_spin, intensity, colour_slot, shutter), fitting the mode's tags and the energy, in turn; sent with the
    # frames as ``a`` for the 3D room to draw a glitterball and pinspots the show has been given (as the director's
    # _appliances_tick would set them).
    appliances: Optional[dict] = None

    def _pick_appliances(self) -> None:
        modes = {t.split(":", 1)[1] for t in self.tags if isinstance(t, str) and t.startswith("modes:")}
        fits = []
        for name, e in (self.cat.scenes or {}).items():
            ap = e.get("appliances") if isinstance(e, dict) else None
            if not isinstance(ap, dict):
                continue
            tags = e.get("tags") or {}
            want = set(tags.get("modes") or ())
            if modes and want and not (modes & want):
                continue
            lo, hi = (tags.get("energy") or [0.0, 1.0])[:2] if isinstance(tags.get("energy"), list) else (0.0, 1.0)
            if not (float(lo) - 1e-9 <= self.energy <= float(hi) + 1e-9):
                continue
            fits.append((name, e))
        if not fits:
            self.appliances = None
            return
        name, e = fits[max(0, self.phrase) % len(fits)]
        self.appliances = {"scene": name, "label": e.get("label") or name, **e["appliances"]}

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

    PLANNED_OUTPUT = "planned"

    def _add_planned(self, space: Optional[dict]) -> None:
        """The planned lights into this preview's own copy of the rig: a space appliance whose kind is a light type and
        no light has its id, and each part of a bar with no light yet (its layout part's type). Each takes the tags,
        pins and settings of a real light of its type (so it joins the groups that one is in), else of any light, on
        the "planned" wire, one after another."""
        if not space:
            return
        fx, types = self.view.get("fixtures") or {}, self.view.get("types") or {}
        want: dict[str, tuple[str, str]] = {}                 # id -> (type, label)
        for aid, a in (space.get("appliances") or {}).items():
            k = a.get("kind") if isinstance(a, dict) else None
            if isinstance(k, str) and k in types and aid not in fx:
                want[aid] = (k, str(a.get("label") or aid))
        lays = space.get("layouts") or {}
        for bid, b in (space.get("bars") or {}).items():
            parts = (lays.get(b.get("layout")) or {}).get("parts") or {}
            for pn, pt in (b.get("parts") or {}).items():
                t = (parts.get(pn) or {}).get("type")
                pid = pt.get("id") if isinstance(pt, dict) else None
                if isinstance(t, str) and t in types and isinstance(pid, str) and pid not in fx:
                    want[pid] = (t, f"{b.get('label') or bid} {pn}")
        if not want:
            return
        anyone = next((f for f in fx.values() if isinstance(f.get("address"), int)), None)
        if anyone is None:
            return
        view = copy.deepcopy(self.view)
        outs = view.setdefault("outputs", {})
        if self.PLANNED_OUTPUT not in outs and anyone.get("output") in outs:
            outs[self.PLANNED_OUTPUT] = {**copy.deepcopy(outs[anyone["output"]]), "id": self.PLANNED_OUTPUT}
        slot = 1
        for pid, (tid, label) in sorted(want.items()):
            fp = int((types.get(tid) or {}).get("footprint") or 1)
            if slot + fp - 1 > 512:
                break
            like = next((f for f in fx.values() if f.get("type") == tid), None)
            tags = [t for t in ((like or {}).get("tags") or []) if not str(t).startswith("bar-")] + ["planned"]
            e = copy.deepcopy(like or anyone)
            e.update({"id": pid, "label": label, "type": tid, "output": self.PLANNED_OUTPUT, "address": slot, "tags": tags,
                      "kind": (types.get(tid) or {}).get("kind") or e.get("kind") or "lamp"})
            if like is None:
                e["pin"] = {}
            view["fixtures"][pid] = e
            self.planned[pid] = (slot - 1, fp)
            slot += fp
        self.view = view

    def planned_frame(self) -> dict[str, bytes]:
        """The planned lights' channels now, by id (after frame(), which moves the clock)."""
        if not self.planned:
            return {}
        fr = self.engine.evaluate(self.PLANNED_OUTPUT, self.now, artnet_lan.CAPS)
        out: dict[str, bytes] = {}
        for fid, (_a, fp) in self.planned.items():
            d = ((fr.get("fixtures") or {}).get(fid) or {}).get("dmx")
            out[fid] = bytes(d[:fp]) if d else bytes(fp)
        return out

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
            pl = await loop.run_in_executor(None, pv.planned_frame)
            msg: dict = {"d": base64.b64encode(d).decode()}
            if pv.appliances is not None:                 # what the show would do with the remote-worked things now
                msg["a"] = pv.appliances
            if pl:                                        # the room's planned lights: drawn only (Room3D's plannedDmx)
                msg["p"] = {k: base64.b64encode(v).decode() for k, v in pl.items()}
            await resp.write(b"data: " + json.dumps(msg).encode() + b"\n\n")
            await asyncio.sleep(1.0 / FPS)
    finally:                                              # the viewer went away (or the engine couldn't be built)
        _live -= 1
