#!/usr/bin/env python3
"""One song through the real show stack on simulated time; writes its timeline (the format score.py and the review
skill read).

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/replay.py <song id> --mode karaoke|live --out OUT.json

What runs, as show_service/__main__.py wires it, nothing re-implemented:
- show-rx: dsp.Features and clock.Clock (madmom inline) on 128-frame blocks, ticked every 10 ms; music.hit to
  Clock.observe_hit and the look engine.
- the ML worker's lines (ml_prepass.py, cached per song), delivered to mllink.MLLink at their turn + a fixed cost.
- show-brain: mllink, structure, cues and director (with its ensemble and treatment) through brain.Brain.add, ticked
  at their rates; the director's target is the mode's feel (karaoke: singalong; live: party, both starting points).
- karaoke mode: karaoke_feed.KaraokeFeed on a fake Flask (the song plays from sample 0 of the song; no lead-in, no
  speaker delay), with its sections, shape, and beat grid / clock override where the code has them.
- the look engine (looks.LookEngine) over the deployment's fixtures, every 50 ms: the mean DMX intensity, and what
  the lights show (form.Describer: how many are on, how the heads move, flicker, colour ...).
Stubbed: the LLM listener (absent), the room (no screens reported live), the screens' renderer (its input, room.direction,
is recorded; its own lag is not modelled), the AI calls (the fake loop never runs a coroutine). Deterministic.
Nothing binds a port or sends a packet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Optional

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_k, "1")
os.environ.setdefault("SHOW_ROOM_DMX_URL", "")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import common  # noqa: E402
import form  # noqa: E402

common.add_repo_path()

from show_service import brain as brainmod  # noqa: E402
from show_service import clock as clockmod  # noqa: E402
from show_service import contract, dsp, mllink  # noqa: E402

FS = 48000
BLOCK = 128
T0 = 1000.0                   # host s of the replay's first sample
WALL0 = 1.8e9                 # the wall clock at T0
PRE_S = 10.0                  # room tone before the song (the gate learns the floor; starting point)
TAIL_S = 6.0                  # after the song
HOP_OUT = 0.05                # outputs sampled every 50 ms
STREAM = "room-mic"
ML_COST_S = {"ced": 0.03, "tags": 0.01, "key": 0.01, "beats": 3.7}   # live p50s (SHOW_ENGINE §6, run_stack): fixed
DMX_LATENCY_S = 0.025         # sent -> visible, when a frame carries no t_eval (unmeasured: starting point)
VIBES = {"karaoke": "singalong", "live": "party"}


class _Handle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Task:
    def cancel(self) -> bool:
        return True

    def done(self) -> bool:
        return True

    def add_done_callback(self, fn) -> None:
        pass

    def cancelled(self) -> bool:
        return True

    def result(self) -> None:
        return None

    def exception(self) -> None:
        return None


class FakeLoop:
    """call_at / call_later / call_soon on simulated time; coroutines (room calls, AI) are closed, never run."""

    def __init__(self, sim: "Sim") -> None:
        self.sim = sim
        self.q: list[tuple[float, int, _Handle, Any, tuple]] = []
        self.n = 0

    def time(self) -> float:
        return self.sim.now

    def call_at(self, when: float, cb, *args) -> _Handle:
        h = _Handle()
        self.n += 1
        self.q.append((float(when), self.n, h, cb, args))
        return h

    def call_later(self, delay: float, cb, *args) -> _Handle:
        return self.call_at(self.sim.now + float(delay), cb, *args)

    def call_soon(self, cb, *args) -> _Handle:
        return self.call_at(self.sim.now, cb, *args)

    call_soon_threadsafe = call_soon

    def create_task(self, coro, **kw) -> _Task:
        try:
            coro.close()
        except Exception:                                   # noqa: BLE001
            pass
        return _Task()

    def run_in_executor(self, ex, fn, *args):
        raise RuntimeError("no executor in the replay")

    def is_running(self) -> bool:
        return True

    def run_due(self, now: float) -> None:
        while True:
            due = [x for x in self.q if x[0] <= now + 1e-9]
            if not due:
                return
            due.sort(key=lambda x: (x[0], x[1]))
            for x in due:
                self.q.remove(x)
            for when, _, h, cb, args in due:
                if not h.cancelled:
                    try:
                        cb(*args)
                    except Exception:                       # noqa: BLE001
                        self.sim.errors["loop:" + getattr(cb, "__name__", "?")] = \
                            self.sim.errors.get("loop:" + getattr(cb, "__name__", "?"), 0) + 1


class Sim:
    def __init__(self, sid: str, mode: str, tmp: pathlib.Path, gain_db: float = 0.0) -> None:
        self.sid, self.mode, self.tmp = sid, mode, tmp
        self.now = T0
        self.errors: dict[str, int] = {}
        self.engine: Any = None
        self.output: Optional[str] = None
        self.events: list = []
        self.song0 = T0 + PRE_S                   # host time of the song's first sample at the mic
        self.lib = common.library()
        self.meta = self.lib.get(sid) or {}
        mix = common.room_mix(sid, FS) * (10.0 ** (gain_db / 20.0))
        rng = np.random.default_rng(1)
        pre = (rng.standard_normal(int(PRE_S * FS)) * 10 ** (-75 / 20)).astype(np.float32)
        tail = (rng.standard_normal(int(TAIL_S * FS)) * 10 ** (-75 / 20)).astype(np.float32)
        self.y = np.concatenate([pre, mix.astype(np.float32), tail])
        self.dur = len(mix) / FS
        self.pubs: list[tuple[float, dict]] = []
        self.decisions: list[tuple[float, str, dict]] = []
        self.clock_bodies: list[tuple[float, dict]] = []
        self._last_key: Optional[tuple] = None
        # show-rx
        kw: dict[str, Any] = {"publish": self._clock_pub, "beat_thread": False}
        try:
            self.clock = clockmod.Clock(**kw, estimator="dbn")
        except TypeError:
            self.clock = clockmod.Clock(**kw)
        self.features = dsp.Features(publish=self._feat_pub)
        self.features.clock_fn = self.clock.snapshot
        # the brain
        ctx = contract.BrainContext(publish=self._pub, features=self.features.latest, clock=self.clock.snapshot,
                                    harmony=lambda: None, piano=self._piano, health=lambda: {},
                                    set_gate_ref=self._gate_ref, rhythm=getattr(self.features, "rhythm", None),
                                    clock_correct=getattr(self.clock, "apply_integer_correction", None),
                                    notify=lambda *a, **k: None)
        self.ctx = ctx
        ctx.loop = self.loop = FakeLoop(self)
        ctx.set_look_out = self._set_look_out
        ctx.section_change = lambda: None
        ctx.set_atmosphere = lambda v: None
        self.kf = None
        if mode == "karaoke":
            self._karaoke()
        self.brain = brainmod.Brain(ctx, state_file=tmp / "state.json", mono=lambda: self.now,
                                    wall=lambda: WALL0 + self.now - T0)
        settings = self.brain._read_settings(first=True) or {}
        ctx.settings = settings
        self.settings = settings
        from show_service import fixtures
        self.registry = fixtures.Registry()               # fixtures.yaml + added + setup, as the rig reads it
        self.registry.poll(settings.get("looks"), lamps=settings.get("lamps"))
        self.rig_view = self.registry.view()
        ctx.rig = lambda: self.rig_view
        from show_service import cues, director, structure
        seed = int(os.environ.get("SM_AD_EVAL_SEED", "0") or 0)
        if seed:                                             # another draw of the same show: a change is judged over
            director.LIGHT_SCENE_SEED, director.LIGHT_FX_SEED, director.ENSEMBLE_SEED = (   # several, not one (a
                1000 * seed, 1000 * seed + 1, 1000 * seed + 2)                             # changed draw count
            random.seed(seed)                                                              # reshuffles the rest)
        self.ml =mllink.attach(ctx, mono=lambda: self.now, log_path=pathlib.Path(os.devnull))
        self.structure = structure.attach(ctx, mono=lambda: self.now)
        self.cues = cues.attach(ctx, mono=lambda: self.now, wall=lambda: WALL0 + self.now - T0,
                                log_path=tmp / "cues.jsonl")
        self.director = director.attach(ctx, mono=lambda: self.now, wall=lambda: WALL0 + self.now - T0,
                                        log_path=tmp / "director.jsonl",
                                        vibes_path=common.REPO / "config" / "room" / "vibes.yaml",
                                        taste_path=tmp / "taste.json")
        orig = self.director._log_event

        def cap(kind: str, detail: dict, _o=orig) -> None:
            try:
                self.decisions.append((self.now, kind, json.loads(json.dumps(detail, default=str))))
            except Exception:                                # noqa: BLE001
                pass
            _o(kind, detail)
        self.director._log_event = cap
        self.comps = [self.ml, self.structure, self.cues, self.director]
        for c in self.comps:
            self.brain.add(c)
        for c in self.comps:
            for step in ("restore", "configure", "start"):
                fn = getattr(c, step, None)
                if fn is None or (c is self.ml and step == "start"):   # never spawn the ML worker
                    continue
                try:
                    fn({}) if step == "restore" else fn(settings) if step == "configure" else fn()
                except Exception as e:                       # noqa: BLE001
                    self.errors[f"{c.name}.{step}"] = 1
                    print(f"  {c.name}.{step} failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            self.director.set_target({"vibe": VIBES[mode], "listening": False}, source="mode")
            self.director.on_mode_change(None, VIBES[mode])
        except Exception as e:                               # noqa: BLE001
            print(f"  director target failed: {e}", file=sys.stderr)
        self._engine()

    # -- wiring -----------------------------------------------------------------------------------------------------
    def _piano(self) -> dict:
        return {"present": False, "stream": None, "source": None, "keys_down": 0, "damper": 0.0,
                "last_note_age_s": None, "velocity": 0.0, "notes_per_s": 0.0, "practice": 0.0}

    def _gate_ref(self, lufs: float) -> None:
        if math.isfinite(float(lufs)):
            self.features.gate_ref_lufs = float(lufs)

    def _pub(self, msg: dict) -> None:
        self.pubs.append((self.now, msg))

    def _clock_pub(self, msg: dict) -> None:
        d = msg.get("data") or {}
        key = tuple(d.get(k) for k in ("bpm", "anchor_ms", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat",
                                       "gate", "epoch", "state"))
        if key != self._last_key:
            self._last_key = key
            self.clock_bodies.append((self.now, d))

    def _feat_pub(self, msg: dict) -> None:
        if msg.get("type") == contract.MUSIC_HIT:
            d = msg["data"]
            try:
                self.clock.observe_hit(d["t_ms"] / 1000.0, d["band"], d["strength"])
            except Exception:                                # noqa: BLE001
                self.errors["observe_hit"] = self.errors.get("observe_hit", 0) + 1
            if self.engine is not None:
                try:
                    self.engine.on_hit(d)
                except Exception:                            # noqa: BLE001
                    self.errors["engine.on_hit"] = self.errors.get("engine.on_hit", 0) + 1

    def _set_look_out(self, **kw: Any) -> None:
        if self.engine is None:
            return
        rk = {k: v for k, v in kw.items() if k in ("hue_depth", "beat_lock", "reset", "beat_depth")}
        if rk:
            try:
                self.engine.set_look(**rk)
            except Exception:                                # noqa: BLE001
                self.errors["engine.set_look"] = self.errors.get("engine.set_look", 0) + 1

    def _karaoke(self) -> None:
        from show_service import karaoke_feed as kfm
        sid = self.sid
        song = dict(self.meta)
        song.pop("lyrics", None)
        song["id"] = sid
        lyr = json.loads((common.song_dir(sid) / "lyrics.json").read_text())

        def fetch(url: str) -> Any:
            if url.endswith("/now"):
                st = "playing" if self.now < self.song0 + self.dur else "stopped"
                return {"player": {"rev": 1, "state": st, "song_id": sid, "qid": "eval", "pos_s": 0.0,
                                   "at": WALL0 + self.song0 - T0},
                        "song": song, "settings": {"child_mode": False, "guide": 1.0}}
            if "/lyrics" in url:
                return lyr
            raise ValueError(url)
        kw: dict[str, Any] = {"fetch": fetch, "clock": lambda: WALL0 + self.now - T0, "mono": lambda: self.now}
        if hasattr(kfm, "library_shape"):
            kw["shape_fn"] = kfm.library_shape
        if hasattr(kfm, "library_grid"):
            kw["grid_fn"] = kfm.library_grid
        if hasattr(kfm, "library_brief") and os.environ.get("SM_AD_EVAL_BRIEF", "1") != "0":
            from show_service import brief as briefmod       # the saved brief only (no AI in a replay): the one
            import smconfig                                  # the room would play this song by; SM_AD_EVAL_BRIEF=0
            kw["brief_fn"] = lambda sid_, _shape=None, _ai=False: briefmod.load(   # replays without it
                smconfig.state("karaoke", "songs", sid_))
        if hasattr(kfm, "library_moments") and os.environ.get("SM_AD_EVAL_MOMENTS", "1") != "0":
            kw["moments_fn"] = kfm.library_moments           # its moments (SM_AD_EVAL_MOMENTS=0: without them)
        try:
            kf = kfm.KaraokeFeed("http://eval.invalid", **kw)
        except TypeError:
            kw.pop("grid_fn", None)
            kf = kfm.KaraokeFeed("http://eval.invalid", **kw)
        self.kf = kf
        self.ctx.now_playing = kfm.chain_now_playing(None, kf.now_playing) if hasattr(kfm, "chain_now_playing") \
            else kf.now_playing
        if hasattr(self.clock, "set_override") and hasattr(kf, "clock_override"):
            self.clock.set_override(kf.clock_override)
        if hasattr(kf, "energy_ahead"):
            try:
                self.ctx.karaoke_energy = kf.energy_ahead
            except Exception:                                # noqa: BLE001
                pass

    def _karaoke_prime(self) -> None:
        """The first poll, and its plan (sections and shape, on a thread live), finished before the song starts."""
        kf = self.kf
        kf.poll_once()
        t_end = time.time() + 120.0
        while time.time() < t_end:
            k, secs = kf._sections
            if k is None or secs:
                break
            for th in threading.enumerate():
                if th.name == "karaoke-shape":
                    th.join(1.0)
            time.sleep(0.05)

    def _engine(self) -> None:
        self.engine = None
        self.output = None
        self.describer = None
        self.shown: list[list[float]] = []
        try:
            from show_service import artnet_lan, light_scenes as ls
            from show_service import space as sp
            from show_service.looks import LookEngine
            view = self.rig_view
            cat = ls.Catalogue()
            cat.poll()
            try:
                space = sp.shared().view()
            except Exception:                                # noqa: BLE001
                space = None
            d = self.director
            eng = LookEngine(palette_fn=d.palette_fn, look_fn=d.look_fn, clock=self.clock,
                             features_fn=self.features.latest, mono=lambda: self.now, light_scenes=cat,
                             space_mod=sp if space is not None else False)
            s = self.settings
            eng.configure(s.get("looks"), view, lamps=s.get("lamps"), lights=s.get("lights"), space=space)
            fx = view.get("fixtures") or {}
            outs = sorted({f.get("output") for f in fx.values() if isinstance(f.get("address"), int)} - {None})
            self.output = outs[0] if outs else None
            self.caps = artnet_lan.CAPS
            self._mode_lights(eng, fx)
            if os.environ.get("SM_AD_EVAL_DMX") == "1" and self.output is not None:
                self._dmx_layout(view)                       # the wire's frames too, for the blind pairs (pairs.py)
            self.engine = eng
            self.describer = form.Describer()                # what the lights show, frame by frame (form.py)
        except Exception as e:                               # noqa: BLE001
            print(f"  look engine not built: {type(e).__name__}: {e}", file=sys.stderr)

    def _mode_lights(self, eng: Any, fixtures: dict) -> None:
        """The room mode's own lights, as room_service sets them in the real room: a karaoke replay is in the mode
        with ``karaoke: true`` (room/modes.json), whose ``"light.<id glob>": "off"`` or ``{"set": {"bri": 0..100}}``
        holds those lights through the song (the house lights: off in Karaoke, owner 26 Nov). Without it the show's
        room-wide effects drove them, and the replay lit the room with house lights the real room never has (owner,
        28 Nov: "why are the house lights on during all these?"). A live replay has no mode's lights."""
        if self.mode != "karaoke":
            return
        import fnmatch
        try:
            import smconfig
            modes = json.loads(pathlib.Path(smconfig.path("room", "modes.json")).read_text()).get("modes") or {}
        except Exception:                                    # noqa: BLE001
            return
        st = next((m.get("state") or {} for m in modes.values() if isinstance(m, dict) and m.get("karaoke")), {})
        for key, val in st.items():
            if not str(key).startswith("light."):
                continue
            if val == "off":
                level = 0.0
            elif isinstance(val, dict) and isinstance((val.get("set") or {}).get("bri"), (int, float)):
                level = max(0.0, min(1.0, float(val["set"]["bri"]) / 100.0))
            else:
                continue
            for fid in fnmatch.filter(list(fixtures), str(key)[len("light."):]):
                try:
                    eng.hold(fid, {"intensity": level}, idle_s=1e7, smooth_ms=0)
                except Exception:                            # noqa: BLE001
                    pass

    # -- the run ----------------------------------------------------------------------------------------------------
    def ml_lines(self) -> list[tuple[float, dict]]:
        ml = self.settings.get("ml") or {}
        h = hashlib.sha1(json.dumps(ml, sort_keys=True, default=str).encode()).hexdigest()[:8]
        cache = common.CACHE / "ml" / f"{self.sid}_{h}_pre{PRE_S:g}.json"
        if not cache.is_file():
            npy = self.tmp / "mic.npy"
            np.save(npy, self.y)
            spec = self.tmp / "ml_spec.json"
            spec.write_text(json.dumps({"audio": str(npy), "t0": T0, "stream": STREAM, "ml": ml,
                                        "models_dir": str(pathlib.Path(ml.get("models_dir") or
                                                                       "~/.cache/screen-machine/models").expanduser())}))
            cache.parent.mkdir(parents=True, exist_ok=True)
            p = subprocess.run(["nice", "-n", "19", str(common.ML_PY), "-I", str(common.HERE / "ml_prepass.py"),
                                str(spec), str(cache)], capture_output=True, text=True)
            if p.returncode != 0:
                print(f"  ML pre-pass failed ({p.returncode}): {p.stderr[-800:]}", file=sys.stderr)
                return []
        lines = json.loads(cache.read_text())["lines"]
        out = []
        for turn, ln in lines:
            out.append((float(turn) + ML_COST_S.get(str(ln.get("type")), 0.05), ln))
        return sorted(out, key=lambda x: x[0])

    def run(self) -> dict:
        ev = self.ml_lines()
        ei = 0
        if self.kf is not None:
            self._karaoke_prime()
        n = len(self.y)
        tick_i, next_tick = 0, T0
        next_brain = T0
        next_slow = T0
        next_out = T0
        next_poll = T0 + 1.0
        brain_dt = 1.0 / contract.BRAIN_TICK_HZ
        out_t: list[float] = []
        lamp: list[float] = []
        direction: list[tuple[float, float, float, float]] = []
        last_dir = (0.0, 0.0, 0.0)
        for j, n0 in enumerate(range(0, n, BLOCK)):
            blk = self.y[n0:n0 + BLOCK]
            arrive = T0 + (n0 + len(blk)) / FS
            while True:
                t_ml = ev[ei][0] if ei < len(ev) else math.inf
                nxt = min(next_tick, t_ml)
                if nxt > arrive:
                    break
                if t_ml < next_tick:
                    self.now = t_ml
                    try:
                        self.ml._on_line(ev[ei][1])
                    except Exception:                        # noqa: BLE001
                        self.errors["ml.line"] = self.errors.get("ml.line", 0) + 1
                    ei += 1
                    continue
                self.now = h = next_tick
                try:
                    self.features.tick(h)
                    self.clock.tick(h)
                except Exception:                            # noqa: BLE001
                    self.errors["rt.tick"] = self.errors.get("rt.tick", 0) + 1
                self.loop.run_due(h)
                if self.kf is not None and h >= next_poll:
                    next_poll += 1.0
                    try:
                        self.kf.poll_once()
                    except Exception:                        # noqa: BLE001
                        self.errors["karaoke.poll"] = self.errors.get("karaoke.poll", 0) + 1
                if h >= next_slow:
                    next_slow += 1.0 / contract.SLOW_TICK_HZ
                    try:
                        self.ml.tick(h)
                    except Exception:                        # noqa: BLE001
                        self.errors["mllink.tick"] = self.errors.get("mllink.tick", 0) + 1
                if h >= next_brain:
                    next_brain += brain_dt
                    for c in (self.structure, self.cues, self.director):
                        try:
                            c.tick(h)
                        except Exception as e:               # noqa: BLE001
                            k = f"{c.name}.tick"
                            if k not in self.errors:
                                print(f"  {k}: {type(e).__name__}: {e}", file=sys.stderr)
                            self.errors[k] = self.errors.get(k, 0) + 1
                    for t_p, m in self.pubs:
                        if m.get("type") == contract.ROOM_DIRECTION:
                            dd = m.get("data") or {}
                            last_dir = (float(dd.get("energy") or 0.0), float(dd.get("intensity") or 0.0),
                                        float(dd.get("pulse_depth") or 0.0))
                    self._keep_events()
                    self.pubs.clear()
                if h >= next_out:
                    next_out += HOP_OUT
                    out_t.append(h)
                    lamp.append(self._lamp(h))
                    direction.append((h, *last_dir))
                tick_i += 1
                next_tick = T0 + tick_i * contract.TICK_S
            flags = contract.AUDIO_SEGMENT if j == 0 else 0
            t0b = T0 + n0 / FS
            self.now = arrive
            pcm = blk.reshape(-1, 1)
            try:
                self.features.process(STREAM, n0, t0b, pcm, FS, flags)
                lf = self.features.latest()
                self.clock.set_level(lf.get("gate") if isinstance(lf, dict) else None, t0b)
                self.clock.process(STREAM, n0, t0b, pcm, FS, flags)
                self.clock.wait_beat()
            except Exception:                                # noqa: BLE001
                self.errors["rt.process"] = self.errors.get("rt.process", 0) + 1
        try:
            self.clock.stop()
        except Exception:                                    # noqa: BLE001
            pass
        return self._timeline(out_t, lamp, direction)

    events: list = []

    def _dmx_layout(self, view: dict) -> None:
        """The universe as artnet_lan lays it out (as show_service/preview.py does): each light's defaults and pins,
        and where its channels go; the hazers are left out (a recording never runs one)."""
        fx, types = view.get("fixtures") or {}, view.get("types") or {}
        base = bytearray(512)
        self.dmx_spans: dict[str, tuple[int, int]] = {}
        for fid, f in fx.items():
            t = types.get(f.get("type")) or {}
            fp, a = int(t.get("footprint") or 0), f.get("address")
            if f.get("output") != self.output or fp <= 0 or not isinstance(a, int) or a < 1 or a + fp - 1 > 512:
                continue
            if "haze" in (f.get("tags") or ()):
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
            self.dmx_spans[str(fid)] = (a - 1, fp)
        self.dmx_base = bytes(base)
        self.dmx: list[bytes] = []

    def _dmx_frame(self, fx: Optional[dict]) -> None:
        if getattr(self, "dmx", None) is None:
            return
        buf = bytearray(self.dmx_base)
        for fid, f in (fx or {}).items():
            sp_ = self.dmx_spans.get(str(fid))
            d = f.get("dmx") if isinstance(f, dict) else None
            if sp_ and d:
                buf[sp_[0]:sp_[0] + min(sp_[1], len(d))] = bytes(d[:sp_[1]])
        self.dmx.append(bytes(buf))

    def _keep_events(self) -> None:
        for t_p, m in self.pubs:
            if m.get("type") in (contract.ROOM_EVENT,):
                self.events.append((t_p, m.get("data") or {}))

    def _lamp(self, h: float) -> float:
        """The lamps' mean level at ``h``; and one row of what the lights show (form.Describer) into ``shown``."""
        fx = None
        if self.engine is not None and self.output is not None:
            try:
                fr = self.engine.evaluate(self.output, h, self.caps)
                fx = fr.get("fixtures") if isinstance(fr, dict) else None
            except Exception:                                # noqa: BLE001
                self.errors["engine.evaluate"] = self.errors.get("engine.evaluate", 0) + 1
        if self.describer is not None:
            self.shown.append(self.describer.row(fx if isinstance(fx, dict) else {}, h))
        self._dmx_frame(fx if isinstance(fx, dict) else None)
        if not isinstance(fx, dict) or not fx:
            return 0.0
        v = [float(f.get("intensity") or 0.0) for f in fx.values() if isinstance(f, dict)]
        return float(np.mean(v)) if v else 0.0

    # -- the timeline -----------------------------------------------------------------------------------------------
    def _s(self, host: float) -> float:
        return round(host - self.song0, 3)

    def _timeline(self, out_t: list, lamp: list, direction: list) -> dict:
        end = T0 + len(self.y) / FS
        fl = flashes(self.clock_bodies, end)
        beats = [self._s(t) for t, g in fl if g in ("beat", "dmx")]
        lock = next((self._s(t) for t, g in fl if g == "beat" and t >= self.song0), None)
        decisions = []
        for h, kind, det in self.decisions:
            at = det.get("at_ms")
            if at is None and isinstance(det.get("section"), dict):
                at = det["section"].get("at_ms")
            if at is None and isinstance(det.get("event"), dict):
                at = det["event"].get("at_ms")
            d = {"t_s": self._s(h), "kind": kind, "why": det.get("why") or det.get("reason"), "detail": det}
            if isinstance(at, (int, float)):
                d["land_s"] = self._s(float(at) / 1000.0)
            decisions.append(d)
        secs = []
        for t_p, e in self.events:
            if e.get("kind") in ("section_change", "look_change", "drop", "track_end", "track_end_armed",
                                 "track_change", "build", "breakdown", "drop_armed"):
                at = e.get("at_ms")
                secs.append({"t_s": self._s(float(at) / 1000.0) if isinstance(at, (int, float)) else self._s(t_p),
                             "announced_s": self._s(t_p), "kind": e.get("kind"), "letter": e.get("letter"),
                             "section_kind": e.get("section_kind"), "detail": e})
        i0 = 0
        while i0 < len(out_t) and out_t[i0] < self.song0:
            i0 += 1
        return {"song": common.song_meta(self.sid, self.lib), "mode": self.mode,
                "run": {"sha": common.git_sha(), "stack": common.stack_id(), "at": time.time()},
                "decisions": decisions,
                "outputs": {"hop_s": HOP_OUT, "t0_s": self._s(out_t[i0]) if i0 < len(out_t) else 0.0,
                            "lamp_level": [round(v, 4) for v in lamp[i0:]],
                            "screen_energy": [round(x[1], 4) for x in direction[i0:]],
                            "screen_intensity": [round(x[2], 4) for x in direction[i0:]],
                            "pulse_depth": [round(x[3], 4) for x in direction[i0:]],
                            **({"shown": {"keys": list(form.KEYS), "v": self.shown[i0:]}}
                               if len(self.shown) == len(out_t) else {})},
                "show": {"beats": [b for b in beats if b >= -1.0], "sections": secs, "lock_s": lock,
                         "bpm": [[self._s(h), b.get("bpm"), b.get("gate")] for h, b in self.clock_bodies]},
                "errors": self.errors,
                **({"_dmx": self.dmx[i0:]} if getattr(self, "dmx", None) is not None
                   and len(self.dmx) == len(out_t) else {})}


def flashes(bodies: list, h_end: float) -> list[tuple]:
    """Integer-beat crossings of the published line, each from the body in force then (beat_eval/run_stack.py's rule,
    artnet_out's REPHASE_SKIP): rows (t, gate)."""
    from show_service.artnet_out import REPHASE_SKIP
    out: list[tuple] = []
    last: Optional[float] = None
    for i, (h, b) in enumerate(bodies):
        h1 = bodies[i + 1][0] if i + 1 < len(bodies) else h_end
        bpm = float(b.get("bpm") or 0.0)
        if bpm <= 0 or h1 <= h or b.get("anchor_ms") is None:
            continue
        P = 60.0 / bpm
        a = float(b["anchor_ms"]) / 1000.0
        k0 = float(b.get("beat_at_anchor") or 0)
        k = math.ceil(k0 + (h - a) / P - 1e-9)
        while True:
            t = a + (k - k0) * P
            if t >= h1:
                break
            if t >= h and (last is None or t > last + REPHASE_SKIP * P):
                out.append((t, b.get("gate")))
                last = t
            k += 1
    return out


def replay(sid: str, mode: str, gain_db: float = 0.0) -> dict:
    with tempfile.TemporaryDirectory(prefix="ad-eval-") as td:
        sim = Sim(sid, mode, pathlib.Path(td), gain_db=gain_db)
        sim.events = []
        t = time.time()
        tl = sim.run()
        tl["run"]["wall_s"] = round(time.time() - t, 1)
        tl["run"]["rt"] = round((time.time() - t) / max(1.0, sim.dur), 3)
        return tl


def dmx_path(out: pathlib.Path) -> pathlib.Path:
    """A timeline's recorded DMX: <id>.<mode>[.s<k>].json -> <id>.<mode>[.s<k>].dmx.npz beside it."""
    return out.with_name(out.name[:-len(".json")] + ".dmx.npz" if out.name.endswith(".json") else out.name + ".dmx.npz")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sid")
    ap.add_argument("--mode", choices=("karaoke", "live"), default="karaoke")
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--gain-db", type=float, default=0.0, help="the mic's level vs the clean mix (starting point 0)")
    ap.add_argument("--ref", action="store_true", help="also put the reference in the timeline")
    a = ap.parse_args()
    try:
        os.nice(19)
    except OSError:
        pass
    tl = replay(a.sid, a.mode, a.gain_db)
    if a.ref:
        import reference
        tl["reference"] = {k: v for k, v in reference.load(a.sid).items() if k not in ("song", "version")}
    out = a.out or (common.CACHE / "replays" / f"{a.sid}.{a.mode}.json")
    dmx = tl.pop("_dmx", None)
    if dmx:                                                  # the wire's frames, one per hop from outputs.t0_s
        out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(dmx_path(out), dmx=np.frombuffer(b"".join(dmx), dtype=np.uint8).reshape(len(dmx), 512),
                            t0_s=np.float64(tl["outputs"]["t0_s"]), hop_s=np.float64(tl["outputs"]["hop_s"]))
    common.write_json(out, tl)
    print(f"{a.sid} {a.mode}: {len(tl['decisions'])} decisions, {len(tl['show']['beats'])} beats, lock "
          f"{tl['show']['lock_s']}, {tl['run']['wall_s']} s (rt {tl['run']['rt']}), errors {tl['errors']} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
