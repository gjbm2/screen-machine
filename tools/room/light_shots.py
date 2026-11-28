"""A picture of every lighting scene, effect and transition, for the Scene library (owner, 27 Nov: "the scene, effect &
transition library should also allow review of the lighting scenes as well as render scenes (with a suitable screen
shot of an indication of what that scene looks like for each)").

How a picture is made, with nothing sent to the room or to show_service:

1. The real look engine (show_service/looks.py) is built here from this deployment's own files (its fixtures, its
   show.yaml, its room model, config/live/light_*.yaml) and run on a made-up clock (120 bpm, a steady beat), one item
   at a time: the light group the item is written for runs it, in a plain look (pars rose, heads sky blue, derbies
   every colour, the laser green), and every other group rests dark, so the picture shows the item and nothing else.
   An effect rides over a steady scene; a transition is caught half way between two scenes.
2. Its frames are packed as the Art-Net output packs them (channel defaults, pins, each fixture's bytes at its
   address) and offered as a virtual node would offer them (GET /dmx, Server-Sent Events) on a port of this tool's own.
3. The 3D room (/lights/room?shot=1: the room alone, a medium haze, the audience's view) is opened in a headless
   browser against that feed, and photographed once the heads have got where the scene sends them.

Pictures land in ~/.cache/sm-lightshots/<deployment>/<kind>/<name>.jpg (kind: scene | effect | transition), with
index.json beside them (what each was taken of). show_service/light_library.py serves them.

    .venv/bin/python tools/room/light_shots.py [--all] [--only NAME ...] [--app http://localhost:5000] [--settle 6]

Without --all only the missing pictures (and those whose catalogue entry changed) are taken. Needs Playwright (the
repo's .venv) and the app running; about 8 s a picture on a software renderer.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from show_service import artnet_lan, config as cfgmod, contract, fixtures, light_scenes as ls, space as sp  # noqa: E402
from show_service.looks import LookEngine  # noqa: E402
from show_service.tests.test_looks import SnapClock, feats, look  # noqa: E402  (the engine tests' made-up clock)

PORT = 8798
OUT = pathlib.Path.home() / ".cache" / "sm-lightshots" / contract.DEPLOYMENT.id
#: the plain look each kind of light shows (0..1): by the group's scenes_for part, then movers, then every other light
COLOURS = {"laser": (0.0, 1.0, 0.0), "derby": (1.0, 1.0, 1.0), "flash": (1.0, 1.0, 1.0), "movers": (0.15, 0.6, 1.0),
           "other": (1.0, 0.2, 0.45)}
STEADY = {"label": "steady", "needs": [], "layers": [], "hits": None, "caps": None, "tags": {}, "appliances": None}
DARK = {**STEADY, "layers": [{"id": "dark", "fx": "dim", "phase": "time", "period_s": 1, "depth": 1.0, "needs": "none"}]}


class Rig:
    """The engine, its groups, and the 512 slots it would send."""

    def __init__(self) -> None:
        self.view = fixtures.load(contract.FIXTURES_FILE)
        self.show = cfgmod.read_show(contract.SHOW_FILE)
        self.cat = ls.Catalogue()
        self.cat.poll()
        if self.cat.error:
            raise SystemExit(f"the light catalogue didn't load: {self.cat.error}")
        # two scenes of the tool's own: steady (under an effect, and the "from" of a transition) and dark (resting)
        self.cat.scenes = {**self.cat.scenes, "shot-steady": STEADY, "shot-dark": DARK}
        try:
            self.space = sp.shared().view()
        except Exception:
            self.space = None
        probs: list[str] = []
        self.lights = ls.parse_lights(self.show.get("lights"), probs)
        self.groups = ls.group_members(self.lights["groups"], self.view, lamps=self.show.get("lamps"))
        self.now = 100.0
        self.look = look()
        self.pal: dict = {}
        self.engine = LookEngine(palette_fn=lambda t: self.pal, look_fn=lambda: self.look,
                                 clock=SnapClock(anchor=100.0, period=0.5), features_fn=lambda: feats(self.now),
                                 mono=lambda: self.now, light_scenes=self.cat,
                                 space_mod=sp if self.space is not None else False)
        self.problems = self.engine.configure(self.show["looks"], self.view, lamps=self.show.get("lamps"),
                                              lights=self.show.get("lights"), space=self.space)
        fx, types = self.view.get("fixtures") or {}, self.view.get("types") or {}
        outs = sorted({f.get("output") for f in fx.values() if isinstance(f.get("address"), int)} - {None})
        if not outs:
            raise SystemExit("no DMX fixtures in this deployment's fixtures.yaml")
        self.output = outs[0]
        self.base = bytearray(512)
        self.spans: dict[str, tuple[int, int]] = {}
        for fid, f in fx.items():                         # as artnet_lan.configure lays the universe out
            t = types.get(f.get("type")) or {}
            fp, a = int(t.get("footprint") or 0), f.get("address")
            if f.get("output") != self.output or fp <= 0 or not isinstance(a, int) or a < 1 or a + fp - 1 > 512:
                continue
            names: dict[str, int] = {}
            for ch in t.get("channels") or []:
                o = ch.get("offset")
                if isinstance(o, int) and 0 <= o < fp:
                    self.base[a - 1 + o] = max(0, min(255, int(ch.get("default", 0) or 0)))
                    names.setdefault(str(ch.get("name")), o)
            for name, v in (f.get("pin") or {}).items():
                if name in names:
                    self.base[a - 1 + names[name]] = max(0, min(255, int(v)))
            self.spans[str(fid)] = (a - 1, fp)
        self.dmx = bytes(self.base)
        self.lock = threading.Lock()
        self.pal = self._palette()

    def _palette(self) -> dict:
        out: dict = {}
        for gid, g in self.groups.items():
            cfg = self.lights["groups"].get(gid) or {}
            part = next((p for p in cfg.get("scenes_for") or () if p in COLOURS), None)
            c = COLOURS[part] if part else COLOURS["movers"] if "pan" in (g.get("caps") or ()) else COLOURS["other"]
            for fid in g["members"]:
                out[fid] = c
        out["*"] = COLOURS["other"]
        return out

    def step(self, dt: float) -> None:
        self.now += dt
        fr = self.engine.evaluate(self.output, self.now, artnet_lan.CAPS)
        buf = bytearray(self.base)
        for fid, f in (fr.get("fixtures") or {}).items():
            sp_ = self.spans.get(fid)
            d = f.get("dmx")
            if sp_ and d:
                buf[sp_[0]:sp_[0] + min(sp_[1], len(d))] = d[:sp_[1]]
        with self.lock:
            self.dmx = bytes(buf)

    # -- what plays where ------------------------------------------------------------------------------------------
    def group_for(self, kind: str, entry: dict) -> list[str]:
        """The groups an item is shown on: every group it is written for (a laser's scene: the lasers; a movers' scene:
        the movers), else the first group that takes it."""
        got = []
        for gid, g in self.groups.items():
            if gid == ls.OTHER_GROUP or not g["members"]:
                continue
            ok = takes(self.cat, entry, g)
            if ok:
                got.append(gid)
        parts = (entry.get("tags") or {}).get("parts")
        if parts or (entry.get("needs") and kind != "transition"):
            return got
        plain = [g for g in got if not (self.lights["groups"].get(g) or {}).get("scenes_for")]
        return plain or got[:1]

    def program(self, on: dict[str, dict]) -> None:
        """Every group's program: ``on[group]`` = {scene, effects?, transition?}; every other group rests dark."""
        at = self.now * 1000.0
        progs = {}
        for gid, g in self.groups.items():
            p = on.get(gid) or {"scene": "shot-dark"}
            tr = p.get("transition") or {"name": "cut", "kind": "cut", "params": {}, "at_ms": at}
            progs[gid] = {"group": gid, "label": g.get("label") or gid, "members": list(g["members"]),
                          "caps": list(g.get("caps") or ()), "scene": p["scene"], "effects": p.get("effects") or [],
                          "transition": tr, "at_ms": tr["at_ms"], "since_ms": at, "why": "a picture",
                          "flow": {"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "shot", "at_ms": at, "screens": {}}}
        self.look = look(light_programs=progs, motion={"speed": 0.5, "amount": 0.6},
                         flow={"dir": "cw", "speed": 0.5, "spread": 0.5, "id": "shot", "at_ms": at, "screens": {}})


def takes(cat, entry: dict, group: dict) -> bool:
    """Whether a light group can show an item: it fits the group's lights, and an item written for one kind of light
    (tags.parts) goes only to a group that takes that part (scenes_for), which takes no other."""
    parts = set((entry.get("tags") or {}).get("parts") or ())
    want = set(group.get("scenes_for") or ())
    return bool(cat.fits(entry, group)) and (bool(parts & want) if (parts or want) else True)


def items(rig: Rig, only: list[str]) -> list[tuple[str, str, dict]]:
    out = [("scene", n, e) for n, e in rig.cat.scenes.items() if not n.startswith("shot-")]
    # a transition's own gesture is listed among the effects under its own name (light_scenes.gesture_name): it is
    # the transition's picture, not an effect's
    gest = {ls.gesture_name(n) for n in rig.cat.transitions}
    out += [("effect", n, e) for n, e in rig.cat.effects.items() if n not in gest]
    out += [("transition", n, e) for n, e in rig.cat.transitions.items()]
    return [x for x in out if not only or x[1] in only]


def stage(rig: Rig, kind: str, name: str, entry: dict) -> tuple[list[str], float]:
    """Put the item on its groups. Returns (the groups, seconds to wait before the picture)."""
    groups = rig.group_for(kind, entry)
    at = rig.now * 1000.0
    if kind == "scene":
        rig.program({g: {"scene": name} for g in groups})
        return groups, 0.0
    if kind == "effect":
        run = {"id": "shot", "name": name, "label": entry.get("label") or name, "at_ms": at + 500.0, "until_ms": None,
               "strength": 1.0, "why": "a picture"}
        rig.program({g: {"scene": "shot-steady", "effects": [run]} for g in groups})
        return groups, 1.0
    # a transition: from a steady scene to a pulse, caught half way through its crossing (or its gesture)
    to = "beat-pulse" if "beat-pulse" in rig.cat.scenes else "shot-steady"
    secs = float(entry.get("seconds") or entry.get("out_s") or 2.0)
    g_ = entry.get("gesture") or {}
    lead = float(g_.get("before_beats") or 0) * 0.5
    when = at + 2000.0 + lead * 1000.0
    tr = {"name": name, "kind": entry.get("kind") or "cut",
          "params": {k: entry[k] for k in ("beats", "seconds", "out_s", "in_s", "wave_beats", "dark_move") if entry.get(k) is not None},
          "at_ms": when}
    gest = []
    if g_:
        gest = [{"id": "shot", "name": ls.gesture_name(name), "label": name,
                 "at_ms": when - lead * 1000.0, "until_ms": when + float(g_.get("after_beats") or 0) * 500.0,
                 "strength": 1.0, "why": "a picture"}]
    rig.program({g: {"scene": "shot-steady"} for g in groups})
    for _ in range(40):
        rig.step(0.05)
    rig.program({g: {"scene": to, "transition": tr, "effects": gest} for g in groups})
    return groups, (when - at) / 1000.0 - 2.0 + min(1.0, secs / 2.0)


class Feed(BaseHTTPRequestHandler):
    rig: Rig

    def log_message(self, *a): pass

    def do_GET(self):
        if self.path.startswith("/status"):
            body = json.dumps({"node": {"universe": 1}, "artnet": {"pps": 40, "age_s": 0, "length": 512}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        if self.path.startswith("/dmx"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    with self.rig.lock:
                        d = self.rig.dmx
                    self.wfile.write(b"data: " + json.dumps({"d": base64.b64encode(d).decode()}).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(0.04)
            except Exception:
                return
        self.send_response(404); self.end_headers()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--all", action="store_true", help="take every picture again")
    ap.add_argument("--only", nargs="*", default=[], help="these names only")
    ap.add_argument("--app", default="http://localhost:5000", help="the app (it serves /lights/room)")
    ap.add_argument("--settle", type=float, default=6.0, help="seconds an item runs before its picture")
    ap.add_argument("--haze", default="medium")
    ap.add_argument("--dry", action="store_true", help="run every item through the engine, take no pictures")
    args = ap.parse_args()
    rig = Rig()
    for p in rig.problems[:5]:
        print("engine:", p)
    OUT.mkdir(parents=True, exist_ok=True)
    idx_file = OUT / "index.json"
    idx = json.loads(idx_file.read_text()) if idx_file.exists() else {}
    todo = []
    for kind, name, entry in items(rig, args.only):
        sig = hashlib.sha1(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()[:12]
        f = OUT / kind / f"{name}.jpg"
        if args.all or args.only or not f.exists() or (idx.get(f"{kind}/{name}") or {}).get("sig") != sig:
            todo.append((kind, name, entry, sig, f))
    print(f"{len(todo)} pictures to take ({OUT})")
    if args.dry:
        for kind, name, entry, _sig, _f in todo:
            groups, wait = stage(rig, kind, name, entry)
            for _ in range(int((2.0 + wait) / 0.05)):
                rig.step(0.05)
            lit = sum(1 for o, n in rig.spans.values() if any(rig.dmx[o:o + n][i] != rig.base[o + i] for i in range(n)))
            print(f"  {kind}/{name}: on {', '.join(groups) or 'NO GROUP'} · {lit} fixtures driven")
        return
    if not todo:
        return
    from playwright.sync_api import sync_playwright
    Feed.rig = rig
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Feed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    running = {"on": True}

    def tick() -> None:                                   # the engine runs in real time, as the room's would
        last = time.monotonic()
        while running["on"]:
            now = time.monotonic()
            try:
                rig.step(min(0.1, now - last))
            except Exception as e:
                print("engine:", type(e).__name__, e)
            last = now
            time.sleep(0.03)
    threading.Thread(target=tick, daemon=True).start()
    gl = os.environ.get("GLARGS", "--use-gl=angle --use-angle=gl-egl --ignore-gpu-blocklist --enable-gpu").split()
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=gl)
        pg = b.new_page(viewport={"width": 640, "height": 360})
        pg.goto(f"{args.app}/lights/room?shot=1&view=front&haze={args.haze}&node=http://127.0.0.1:{PORT}",
                wait_until="domcontentloaded")
        pg.wait_for_selector("canvas", timeout=30000)
        time.sleep(5)
        for i, (kind, name, entry, sig, f) in enumerate(todo, 1):
            groups, wait = stage(rig, kind, name, entry)
            if not groups:
                print(f"  [{i}/{len(todo)}] {kind}/{name}: no light group here takes it: no picture")
                idx[f"{kind}/{name}"] = {"sig": sig, "groups": [], "at": None}
                continue
            time.sleep((args.settle if kind != "transition" else 2.0) + wait)
            f.parent.mkdir(parents=True, exist_ok=True)
            png = f.with_suffix(".png")
            best = b""
            for k in range(1 if kind == "scene" else 3):  # an effect may flash: of three moments, the one with most in it
                if k:
                    time.sleep(0.37)
                shot = pg.locator("canvas").screenshot()
                best = shot if len(shot) > len(best) else best
            png.write_bytes(best)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-q:v", "3", str(f)], check=True)
            png.unlink(missing_ok=True)
            idx[f"{kind}/{name}"] = {"sig": sig, "groups": groups, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            idx_file.write_text(json.dumps(idx, indent=1))
            print(f"  [{i}/{len(todo)}] {kind}/{name}: on {', '.join(groups)}")
        b.close()
    running["on"] = False


if __name__ == "__main__":
    main()
