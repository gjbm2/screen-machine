"""``python -m show_service``: wire the receiver, DSP, beat clock, piano, harmony, Art-Net out, the bus, (Stage 3)
the brain and (Stage 4) the ML worker's feed, then keep them running.

```
 [show-rx]   Receiver (receiver.py): recv -> on_audio -> Features.process, then Clock.process, then Rings.write, then
                                                         MLFeed.write (the active stream's blocks only, the same
                                                         block, each under its own guard)
                                            on_midi  -> NoteTap.on_midi, Recorder.on_midi (a take's append; guard
                                                        "recorder"), then Piano(stream).on_midi (one Piano per
                                                        MIDI stream, lazily; state_channels: show.yaml
                                                        situations.midi.channels, the keys' channel)
                                            on_tick  -> Piano.tick, the voice, Features.tick, Harmony.tick,
                                                        Clock.tick, snapshot (+ the PianoView the brain reads)
             Features' music.hit bodies also go to Clock.observe_hit (sub/bass hits refine the beat phase);
             Features.clock_fn = Clock.snapshot (the medium lane's swing and syncopation)
 [show-beat] Clock's activation (madmom) off show-rx: only with --beat-thread on / SHOW_BEAT_THREAD=on (Stage 4;
             off by default: measured, it cost more than it saved; README S4.12)
 [show-beat-sup] with --beat-proc on / SHOW_BEAT_PROC=on (wins over the thread): madmom in a child, python -m
             show_service.beatproc (<state_dir>/beat-worker.log); show-rx feeds it hops through a pipe, inline until
             it says hello and whenever it is gone; mllink counts its CPU in show_service's share (child_pids)
 Receiver, Features and Clock take SHOW_RX_OFF's rollbacks (config.RX_FEATURES) and SHOW_RX_COALESCE_MIN.
 [show-dns]  started by Receiver.start(): sender names re-resolved every 30 s (at once after set_senders)
 [show-bus]  Bus (bus.py): WS fan-out, show.state 25 Hz, /healthz, /api/v1/state (state() below, from snapshots),
             and api.py's routes and /v1/events (they reach the brain only through Brain.call and snapshots)
 [show-brain] Brain (brain.py): room, situations, autopilot, sources, ml, listener (1 Hz), structure, cues and
             director (10 Hz); ml (mllink.MLLink) spawns and supervises the worker child (python -m show_service.ml.worker in
             ~/.venvs/sm-show-ml, nice 19), attaches its stdin to the MLFeed, and turns Beat This! votes into
             Clock.apply_integer_correction
 [show-artnet], [show-artnet-poll]  artnet_out.VirtualMaestro, only with a target (off by default): reads
             Features.latest, the clock and Director.palette_fn by reference
 [main]      signals; latency.json polled every 2 s -> Receiver.set_offsets, audio_cal.json -> Features.gate_offsets;
             gc.freeze() (no collect) after warm-up
```

* **The brain** (README "Stage 3", S3.4) is on unless ``--no-brain`` / ``SHOW_BRAIN=off``; off, the process is exactly
  as before Stage 3 (no taps, no ring, no Stage-3 routes: the rollback switch). Its files come from the options:
  ``show_file``, ``vibes_file``, and ``state_dir`` (state.json, director/autopilot/listener jsonl, the test children's
  logs). The engine side of ``contract.BrainContext`` is filled here (README S3.4 "Engine-side slots"); the
  components fill the rest through ``Brain.add``. The director's ``palette_fn`` is Art-Net's palette (None while
  neutral: Art-Net keeps its fixed colour), and ``set_look`` / ``section_change`` go back to the director.

* **One piano voice on the bus.** Every Piano keeps its table, but only one publishes music.note/pedal/notes (midi.py
  "Voice"): the present one, live before test (the same pick as harmony's). A new Piano starts muted. The packet that
  makes a better piano present hands it the voice at once (``on_midi``); otherwise ``on_tick`` re-picks after the
  pianos tick. With no piano present the last voice keeps it (quiet after its present=False frame). So the renderer
  (sound_view: one sess at a time) sees one sess, and a hand-over is one clean session change.

* Modules are used only through show_service/README.md's interfaces and imported lazily: one that is missing or
  fails to import or construct is logged and left out (degrade, never refuse). The bus and /healthz always come
  up, so scripts/ctl and the Spectre card can see it.
* Every callback into dsp/midi/harmony/clock runs under a guard (``features.process``, ``clock.process``,
  ``clock.observe_hit``, ``on_midi``, ``piano.tick``, ``features.tick``, ``harmony.tick``, ``clock.tick``,
  ``snapshot``): an exception is counted and logged at most every 30 s per callback, and show-rx carries on.
* Rates live in the modules: music.features 25 Hz (Features.tick), music.harmony 10 Hz (Harmony.tick, from the
  present piano, live before test, else the audio chroma), music.notes 2-10 Hz (Piano.tick), music.note/pedal at
  once, music.clock on every re-anchor + 10 Hz (Clock.tick; clock.py, the §5.3 beat clock).
* Snapshots: show-rx swaps a dict (pianos, Features/Harmony stats, clock) in by reference every 0.5 s; the API reads
  only that, Receiver.health() and the bus's latest messages.
* Art-Net out (README "artnet_out.py"): ``VirtualMaestro(**vars(config_from_env()), target=<config's
  --artnet-target over SHOW_ARTNET_TARGET>, clock=<clock if it has next_beat>, features_fn=Features.latest,
  palette_fn=None)``; its status() is in the state as ``artnet``.
* Process: BLAS at one thread (set before numpy loads), ``sys.setswitchinterval(0.001)``. ``Show.start(freeze_gc=
  True)`` runs one ``gc.collect(); gc.freeze()`` after building every module and before show-rx and show-artnet
  start (a full collection holds the GIL for about 50 ms); ``gc.freeze()`` alone (cheap, no collection) after
  WARMUP_S. Logs go to stderr (scripts/ctl owns the log file). SIGTERM/SIGINT: stop the brain (its children: the
  test sources and the ML worker), Art-Net out, rx, show-beat, then the bus; exit 0.
* **Stage 4** (README S4.5): with the brain, ``mllink.MLFeed`` (show-rx, guard "ml.feed") and the context's engine
  slots ``rhythm`` (Features.rhythm), ``ml_feed`` and ``clock_correct`` (Clock.apply_integer_correction);
  ``/api/v1/state`` gains ``ml`` (the MLLink snapshot, also under brain.components.ml).
* **The mic on demand** (README S5.16, mic.py): ``Mic`` decides which room-sound streams capture (Auto on, or the
  mode's ``show.mic`` claim; the Room page's Mute wins). ``attach(rx)`` decides before show-rx starts, so the first
  SUBSCRIBE carries it; with the brain it also gets the ring (``Rings.set_off``), Auto's live on/off
  (``Autopilot.is_on``, which wakes it on a toggle), the room view (the mode's label) and the card's refresh. Its
  routes ride ``_routes`` like inputs.py's; the card's ``mic_state`` is ``Mic.state()``. A mic.py that fails to load
  leaves the receiver holding every room mic off (privacy first), logged.
* **Stage 5** (README S5.4): with the brain, the rig (``outputs.attach``: the fixture registry, the look engine and
  the drivers, every output off until config/room/fixtures.yaml enables it) joins the brain last, after the
  director, fed by reference: ``palette_fn`` and ``look_fn`` (the director's), the clock, ``Features.latest`` and
  artnet_out's neutral colour and depth. ``ctx.set_look_out`` fans out to Art-Net out's ``set_look`` and the rig's
  (each guarded, each with only its own keys: the composer's ``plan`` is Art-Net out's, ``beat_depth`` the rig's;
  README S5.18); the ``music.hit`` bodies that reach ``Clock.observe_hit`` also reach ``Rig.on_hit`` (guard
  "rig.hit"). The registry reads config/room/fixtures.yaml (with the Lights page's fixtures.added.yaml merged under
  it, README S5.23), the latency file (``--latency-file``) and artnet_out's patch.yaml; nodes.json and follow.json
  (who follows the music) go to the state dir. ``/api/v1/state`` gains ``rig`` (the Rig snapshot). Stop: the brain
  stops the rig first (runners, then drivers: Hue gives its area back), then Art-Net out, rx, bus.
"""

from __future__ import annotations

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")    # BLAS at one thread (§2), before anything imports numpy

import collections  # noqa: E402
import gc  # noqa: E402
import importlib  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
import pathlib  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from typing import Any, Callable, Mapping, Optional  # noqa: E402

from . import __version__, config, contract  # noqa: E402
from .bus import Bus  # noqa: E402

log = logging.getLogger("show_service")

RELOAD_S = 2.0          # latency.json mtime poll
VM_LOOK_KEYS = ("hue_depth", "beat_lock", "reset", "plan")   # artnet_out's set_look (plan: the composer's, S5.18)
RIG_LOOK_KEYS = ("hue_depth", "beat_lock", "reset", "beat_depth")   # the rig's: beat_depth too (S5.14), no plan
WARMUP_S = 10.0         # then gc.freeze()
SNAPSHOT_S = 0.5        # show-rx -> API snapshot
GUARD_LOG_S = 30.0
PIANO_VEL_TAU_S = 2.0         # PianoView.velocity: EMA of strike velocities (README S3.4)
PIANO_RATE_WINDOW_S = 4.0     # PianoView.notes_per_s: strikes in the last 4 s / 4
RX_COST_N = 6000              # show-rx's own cost per on_audio block and per on_tick (the state's rx_ms; ~16 s)
#: Brain component modules, in add order (README S3.2, S4.2, S5.4 and S5.22: room, situations, autopilot, sources, ml,
#: structure, listener, cues, director, rig; autopilot.attach returns the first three, mllink.attach the ml component,
#: cues.attach the cue engine (it fires what the listener arms), outputs.attach the rig, after the director: it reads
#: the director's palette_fn and look_fn; recorder.attach the Room page's Record, 22 Oct: on_midi taps it after NoteTap;
#: playlist.attach the Music page's player, 22 Oct: after sources and recorder, it drives the test piano)
BRAIN_MODULES = ("autopilot", "sources", "recorder", "playlist", "mllink", "structure", "listener", "cues", "director",
                 "outputs")
NEUTRAL_PIANO_VIEW: contract.PianoView = {"present": False, "stream": None, "source": None, "keys_down": 0,
                                          "damper": 0.0, "last_note_age_s": None, "velocity": 0.0, "notes_per_s": 0.0}


def _inputs(h: dict) -> dict:
    """The card's connection words (api.card): permanent (not on-demand) senders configured, and each room sound
    stream's state (not MIDI, not test streams)."""
    senders = h.get("senders") if isinstance(h.get("senders"), dict) else {}
    streams = h.get("streams") if isinstance(h.get("streams"), dict) else {}
    return {"senders": sum(1 for v in senders.values() if isinstance(v, dict) and not v.get("on_demand")),
            "room": {n: v.get("state") for n, v in streams.items()
                     if isinstance(v, dict) and v.get("kind") != "midi" and not v.get("test")}}


def _input(h: dict) -> Optional[dict]:
    """The active stream's raw meter for the card (api.card's hearing.input / input_trace): receiver health's audio
    peak/rms over the last METER_S and its 10 Hz trace; None without an active stream."""
    streams = h.get("streams") if isinstance(h.get("streams"), dict) else {}
    st = streams.get(h.get("active_audio")) if isinstance(h.get("active_audio"), str) else None
    audio = st.get("audio") if isinstance(st, dict) else None
    if not isinstance(audio, dict):
        return None
    return {"peak_dbfs": audio.get("peak_dbfs"), "rms_dbfs": audio.get("rms_dbfs"), "trace": audio.get("trace")}


class _Guard:
    """Counts a callback's exceptions and logs the first, then at most one a GUARD_LOG_S."""
    __slots__ = ("name", "errors", "_next")

    def __init__(self, name: str) -> None:
        self.name, self.errors, self._next = name, 0, 0.0

    def failed(self) -> None:
        self.errors += 1
        now = time.monotonic()
        if now >= self._next:
            self._next = now + GUARD_LOG_S
            log.error("%s failed (%d so far; logged at most every %.0f s)", self.name, self.errors, GUARD_LOG_S,
                      exc_info=True)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        v = getattr(obj, name)
        return v() if callable(v) else v
    except Exception:
        return default


def _pcts(dq: collections.deque) -> Optional[dict]:
    """{p50, p95, max, n} of a deque of ms (read on show-bus: a copy first; None while empty)."""
    vals = sorted(list(dq))
    if not vals:
        return None
    n = len(vals)
    return {"p50": round(vals[n // 2], 3), "p95": round(vals[min(n - 1, int(0.95 * n))], 3),
            "max": round(vals[-1], 3), "n": n}


def _is_test(stream: str) -> bool:
    return stream in contract.TEST_STREAMS or stream.startswith("test-")


def _set_voice(piano: Any, on: bool, now_s: float) -> None:
    """midi.Piano.set_voice; a Piano without one (an older midi.py) always publishes."""
    fn = getattr(piano, "set_voice", None)
    if fn is not None:
        fn(on, now_s)


def _present_at(piano: Any, now_s: float) -> bool:
    fn = getattr(piano, "present_at", None)
    return bool(fn(now_s)) if fn is not None else bool(getattr(piano, "present", False))


class Show:
    """The wiring. Construct, ``start()``, then the main thread calls ``set_offsets`` and finally ``stop()``."""

    def __init__(self, opts: config.Options, *, brain_kw: Optional[Mapping[str, Mapping[str, Any]]] = None) -> None:
        """``brain_kw``: extra keyword arguments per Stage-3 module's ``attach`` (tests: a fake LLM for the listener,
        fake clocks); production passes none."""
        self.opts, self.cfg = opts, opts.config
        self.t0 = time.monotonic()
        self.modules: dict[str, str] = {}
        self.offsets: dict[str, float] = {}
        self.ready = False
        # Stage 3 (None with --no-brain, or when its module is missing)
        self.brain: Any = None
        self.ctx: Optional[contract.BrainContext] = None
        self.rings: Any = None
        self.tap: Any = None
        self.director: Any = None
        self.api: Any = None
        self.mlfeed: Any = None                      # Stage 4: mllink.MLFeed (show-rx writer), with the brain only
        self.ml: Any = None                          # Stage 4: the MLLink component
        self.rig: Any = None                         # Stage 5: outputs.Rig (the rig component), with the brain only
        self.recorder: Any = None                    # recorder.Recorder (the Room page's Record; on_midi's tap)
        self._runtime_offsets: dict[str, float] = {}   # sources.py's (a rendered test-piano run), over latency.json's
        self._offsets_lock = threading.Lock()        # main thread (latency.json) vs show-brain (sources)
        self._out_cfg: dict = {}                     # artnet_out's config (the rig's neutral colour, patch.yaml)
        self._amod: Any = None                       # the artnet_out module (its DEFAULT_RGB, HUE_DEPTH)
        self._brain_kw = {k: dict(v) for k, v in (brain_kw or {}).items()}
        self._pview: contract.PianoView = dict(NEUTRAL_PIANO_VIEW)        # type: ignore[assignment]
        self._vel_ema: Optional[float] = None
        self._vel_t: Optional[float] = None
        self._pv_last = 0.0
        self._static_settings: dict = {}             # show.yaml read once, for --no-brain (the brain re-reads it)
        if not opts.brain:
            self._static_settings = self._read_show_file()
        if opts.brain:
            amod = self._import("api")
            if amod is not None:
                self.api = self._make("api", lambda: amod.routes(lambda: self.brain, self._engine,
                                                                 log_files=self._log_files()))
        from . import levels as levels_mod             # the live input meter, WS /v1/levels (05, 16 Oct)
        self.levels = levels_mod.LevelFeed()
        from . import inputs as inputs_mod             # the Room page's Mic choice, GET/PUT /api/v1/input(s)
        self.inputs = inputs_mod.Inputs(self.health, show_file=self.opts.show_file,
                                        state_file=pathlib.Path(self.opts.state_dir) / "input.json")
        self.mic: Any = None                           # the mic on demand, GET/PUT /api/v1/mic (README S5.16)
        mic_mod = self._import("mic")
        if mic_mod is not None:
            self.mic = self._make("mic", lambda: mic_mod.Mic(self.inputs, self.health,
                                                             state_file=pathlib.Path(self.opts.state_dir) / "mic.json"))
        if self.mic is None:
            log.error("no mic.py: the receiver keeps every room mic off (privacy first); MIDI and tests as usual")
        # the Music page (8f, 22 Oct): the library (GET /api/v1/library...; recordings through the recorder component)
        # and, with the brain, the player's routes (GET/POST /api/v1/player, /api/v1/playlists: Brain.call)
        self.library: Any = None
        try:
            from . import library as lib_mod             # like levels/inputs: not one of the engine's modules
            self.library = lib_mod.Library(state_dir=pathlib.Path(self.opts.state_dir), recordings_fn=self._recordings,
                                           rec_dir_fn=self._recordings_folder,
                                           recorder_ops=lib_mod.RecorderOps(lambda: self.brain))
        except Exception:
            log.exception("library.py not loaded: the Music page has no library (the show carries on)")
        pl_mod = self._import("playlist") if opts.brain else None
        # whole-piece scores for the Music page (1c, 23 Oct): GET /api/v1/score/<piece> (score_sheet.py)
        self.sheets: Any = None
        if self.library is not None:
            try:
                from . import score_sheet as sheet_mod
                self.sheets = sheet_mod.ScoreSheets(self.library)
            except Exception:
                log.exception("score_sheet.py not loaded: no scores on the Music page (the show carries on)")

        # the piano on a phone or iPad (1c, 23 Oct): GET /api/v1/piano/live.mp3, fed from on_midi (piano_live.py)
        self.piano_live: Any = None
        try:
            from . import piano_live as live_mod
            self.piano_live = live_mod.PianoLive(output_fn=self._piano_output, pos_fn=self._piano_pos)
        except Exception:
            log.exception("piano_live.py not loaded: no piano stream for devices (the show carries on)")

        def _routes(app: Any, api: Any = self.api, lv: Any = self.levels, inp: Any = self.inputs,
                    mc: Any = self.mic, lib: Any = self.library, plm: Any = pl_mod, sh: Any = self.sheets,
                    pv: Any = self.piano_live) -> None:
            lv.routes(app)
            inp.routes(app)
            if mc is not None:
                mc.routes(app)
            if lib is not None:
                lib.routes(app)
            if sh is not None:
                sh.routes(app)
            if pv is not None:
                pv.routes(app)
            if plm is not None:
                plm.routes(app, lambda: self.brain)
            if api is not None:
                api(app)
        self.bus = Bus(self.cfg.bus_bind[0], self.cfg.bus_bind[1], state_fn=self.state, health_fn=self.health,
                       routes=_routes)
        self.publish: contract.Publish = self.bus.publish
        if self.api is not None:
            self.api.publish = self.publish            # room.why (api.EventsHub's 1 Hz loop)
        self.rx: Any = None
        self.features: Any = None
        self.harmony: Any = None
        self.clock: Any = None
        self.artnet: Any = None
        self.pianos: dict[str, Any] = {}
        self._voice: Any = None              # the one Piano that publishes (on_tick / on_midi)
        self._midi_mod: Any = None
        self._snap: dict = {}
        self._snap_due = 0.0
        self._rx_audio_ms: collections.deque = collections.deque(maxlen=RX_COST_N)
        self._rx_tick_ms: collections.deque = collections.deque(maxlen=RX_COST_N)
        self._g = {k: _Guard(k) for k in ("features.process", "clock.process", "clock.observe_hit", "on_midi",
                                          "piano.tick", "features.tick", "harmony.tick", "clock.tick", "snapshot",
                                          "ring.write", "notetap", "piano.view", "ml.feed", "rig.hit",
                                          "artnet.set_look", "rig.set_look", "recorder", "piano_live")}

    # -- building (main thread) ----------------------------------------------------------------------------------
    def _read_show_file(self) -> dict:
        """show.yaml, best effort (--no-brain only: the MIDI state channels). Missing or bad: {} (all channels)."""
        try:
            import yaml
            data = yaml.safe_load(pathlib.Path(self.opts.show_file).read_text())
            return data if isinstance(data, dict) else {}
        except Exception as e:
            log.warning("show.yaml not read (%s): MIDI states describe every channel", e)
            return {}

    def _import(self, name: str) -> Any:
        try:
            mod = importlib.import_module(f"{__package__ or 'show_service'}.{name}")
        except Exception as e:
            self.modules[name] = f"missing: {type(e).__name__}: {e}"
            log.error("%s.py not loaded (%s): running without it", name, e)
            return None
        self.modules[name] = "ok"
        return mod

    def _make(self, name: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as e:
            self.modules[name] = f"failed: {type(e).__name__}: {e}"
            log.exception("%s could not be built: running without it", name)
            return None

    def start(self, *, freeze_gc: bool = False) -> None:
        """Build every module, then start rx and Art-Net out. ``freeze_gc`` (main() passes True): one full
        ``gc.collect()`` + ``gc.freeze()`` after building, before show-rx and show-artnet exist, so the collection's
        GIL hold (about 50 ms with madmom and aiohttp loaded) never lands on the hot path."""
        self.bus.start()
        if self.bus.bind_error:
            log.error("bus not bound yet (%s): it keeps retrying", self.bus.bind_error)
        cmod = self._import("clock")         # before dsp: Features' hits feed the clock
        if cmod is not None:
            off = self.opts.rx_off
            self.clock = self._make("clock", lambda: cmod.Clock(
                publish=self.publish, beat_thread=self.opts.beat_thread, beat_proc=self.opts.beat_proc,
                beat_nice=self.opts.beat_nice, beat_log=pathlib.Path(self.opts.state_dir) / "beat-worker.log",
                gap_skip="gapskip" not in off, agc_chunks="agc" not in off,
                estimator=getattr(self.opts, "clock_estimator", "dbn")))
        dsp = self._import("dsp")
        if dsp is not None:
            self.features = self._make("dsp", lambda: dsp.Features(publish=self._features_publish,
                                                                   batch="batch" not in self.opts.rx_off))
        if self.features is not None and self.clock is not None and hasattr(self.clock, "snapshot"):
            try:
                self.features.clock_fn = self.clock.snapshot     # the medium lane's swing/syncopation (S4.6)
            except Exception:
                log.exception("dsp: clock_fn not set: swing and syncopation read None")
        self._midi_mod = self._import("midi")
        hmod = self._import("harmony")
        if hmod is not None:
            self.harmony = self._make("harmony", lambda: hmod.Harmony(self.publish))
        rmod = self._import("receiver")
        if rmod is not None:
            off = self.opts.rx_off
            self.rx = self._make("receiver", lambda: rmod.Receiver(self.cfg, on_audio=self.on_audio,
                                                                    on_midi=self.on_midi, on_tick=self.on_tick,
                                                                    on_level=self.levels.push,
                                                                    coalesce="coalesce" not in off,
                                                                    coalesce_min=self.opts.rx_coalesce_min,
                                                                    kernel_stamps="stamps" not in off,
                                                                    split_midi="split" not in off))
        if self.rx is not None:
            self.inputs.attach(self.rx)
            if hasattr(self.rx, "set_level_watch"):     # the live meter's ?stream=<name>: show-rx pushes it too
                self.levels.on_watch = self.rx.set_level_watch
            if self.mic is not None:
                self.mic.attach(self.rx)                 # decided before show-rx starts: the first SUBSCRIBE has it
        amod = self._import("artnet_out")
        out_cfg: dict = {}
        if amod is not None:
            try:
                out_cfg = dict(vars(amod.config_from_env()))
            except Exception as e:           # a bad SHOW_PATCH_FILE / SHOW_HUE_LATENCY_MS: its defaults
                log.error("Art-Net out env: %s: using its defaults", e)
                out_cfg = {}
        self._out_cfg, self._amod = dict(out_cfg), amod
        if self.opts.brain:
            self._build_brain(out_cfg.get("patch_path"))
            self._wire_mic()
        if amod is not None:
            self.artnet = self._make("artnet_out", lambda: self._virtual_maestro(amod, out_cfg))
        if self.ctx is not None and self.artnet is not None:
            self.ctx.section_change = getattr(self.artnet, "section_change", None)
        if self.ctx is not None and (getattr(self.artnet, "set_look", None) is not None
                                     or getattr(self.rig, "set_look", None) is not None):
            self.ctx.set_look_out = self._set_look_out     # Stage 5: Art-Net out's set_look, then the rig's
        if freeze_gc:
            t = time.perf_counter()
            gc.collect()
            gc.freeze()                      # the built modules out of every later collection (§2)
            log.info("gc: collected and froze %d objects in %.0f ms, before the hot threads start",
                     gc.get_freeze_count(), (time.perf_counter() - t) * 1000.0)
        if self.rx is not None:
            try:
                self.rx.start()
            except Exception as e:
                self.modules["receiver"] = f"failed: {type(e).__name__}: {e}"
                log.exception("receiver did not start: running without it")
        if self.artnet is not None:
            try:
                self.artnet.start()
            except Exception as e:
                self.modules["artnet_out"] = f"failed: {type(e).__name__}: {e}"
                log.exception("Art-Net out did not start: running without it")
        if self.mic is not None:
            self.mic.start()                             # show-mic: re-decides at 2 Hz and on a wake
        if self.brain is not None:
            try:
                self.brain.start()
            except Exception as e:
                self.modules["brain"] = f"failed: {type(e).__name__}: {e}"
                log.exception("the brain did not start: running without it")
        self.ready = True
        log.info("show_service %s up: bus %s, vban %s:%d, senders %s, modules %s, beat activation %s", __version__,
                 self.bus.url, self.cfg.vban_bind[0], self.cfg.vban_bind[1],
                 ", ".join(f"{s.name}={s.host}:{s.port}/{','.join(s.streams)}" for s in self.cfg.senders) or "none",
                 ", ".join(f"{k} {v}" for k, v in self.modules.items()),
                 _attr(getattr(self.clock, "act", None), "name", "none"))

    def _virtual_maestro(self, amod: Any, out_cfg: dict) -> Any:
        kw = dict(out_cfg)
        kw["target"] = self.opts.artnet_target          # config.py: --artnet-target over SHOW_ARTNET_TARGET
        clock = self.clock if hasattr(self.clock, "next_beat") else None
        features_fn = self.features.latest if self.features is not None else None
        # the director's look (None: the fixed colour); maestro_palette_fn carries the drop's hit as Hue takes it, since
        # the virtual Maestro's lamp channels are what room streams to Hue (README S5.14)
        palette_fn = getattr(self.director, "maestro_palette_fn", None) or getattr(self.director, "palette_fn", None)
        return amod.VirtualMaestro(**kw, clock=clock, features_fn=features_fn, palette_fn=palette_fn)

    # -- Stage 3: the brain (main thread, before rx and the brain start) ------------------------------------------
    def _log_files(self) -> dict:
        d = pathlib.Path(self.opts.state_dir)
        return {"director": d / "director.jsonl", "autopilot": d / "autopilot.jsonl",
                "listener": d / "listener.jsonl", "cues": d / "cues.jsonl"}

    def _component_kw(self, name: str, patch_path: Any) -> dict:
        logs = self._log_files()
        kw: dict[str, Any] = {
            "autopilot": {"log_path": logs["autopilot"]},
            "sources": {"log_dir": pathlib.Path(self.opts.state_dir), "devices_fn": self._piano_devices},
            "listener": {"log_path": logs["listener"],
                         "clips_dir": pathlib.Path(self.opts.state_dir) / "listen" / "clips"},   # 09, 21 Oct
            "cues": {"log_path": logs["cues"]},
            "mllink": {"log_path": pathlib.Path(self.opts.state_dir) / "ml-worker.log",
                       "child_pids": self._child_pids},
            "director": {"log_path": logs["director"], "vibes_path": pathlib.Path(self.opts.vibes_file),
                         **({"patch_path": pathlib.Path(patch_path)} if patch_path else {})},
        }.get(name, {})
        if name == "outputs":
            kw = self._rig_kw(patch_path)
        if name == "playlist":                           # the Music page's player: it drives sources' test piano
            kw = {"state_dir": pathlib.Path(self.opts.state_dir), "library": self.library,
                  "sources_fn": lambda: self._component("sources")}
        extra = dict(self._brain_kw.get(name, {}))
        if name == "outputs" and isinstance(extra.get("driver_kw"), dict):       # per driver kind, on top
            dk = {k: dict(v) for k, v in (kw.get("driver_kw") or {}).items()}
            for kind, v in extra.pop("driver_kw").items():
                dk[kind] = {**dk.get(kind, {}), **dict(v)}
            kw["driver_kw"] = dk
        kw.update(extra)
        return kw

    def _piano_output(self) -> Optional[str]:
        """Where the piano is heard now (piano_live's gate): the test piano's output while it plays, else the music
        player's chosen one (PC22 when it has none)."""
        src = self._component("sources")
        try:
            st = (src.status() or {}).get("piano") if src is not None else None
        except Exception:                                # noqa: BLE001
            st = None
        if isinstance(st, dict) and st.get("state") in ("starting", "preparing", "playing") and st.get("output"):
            return str(st["output"])
        pl = self._component("playlist")
        out = getattr(pl, "output", None)
        return out if isinstance(out, str) and out else "pc22"

    def _piano_pos(self) -> Optional[dict]:
        """Where the test piano is in its piece now (piano_live's map for a device's own light): {piece, pos_s, at
        (host CLOCK_MONOTONIC), speed, state}, or None while it plays nothing."""
        src = self._component("sources")
        try:
            st = (src.status() or {}).get("piano") if src is not None else None
        except Exception:                                # noqa: BLE001
            return None
        if not isinstance(st, dict) or not st.get("piece"):
            return None
        pos = st.get("pos_s")
        return {"piece": st.get("piece"), "pos_s": pos if isinstance(pos, (int, float)) else None,
                "at": round(time.monotonic(), 4), "speed": st.get("speed") or 1.0, "state": st.get("state")}

    def _piano_devices(self) -> list:
        live = self.piano_live
        return live.device_outputs() if live is not None else []

    def _component(self, name: str) -> Any:
        comps = getattr(self.brain, "components", None)
        return comps.get(name) if isinstance(comps, dict) else None

    def _recordings(self) -> list:
        """The library's ``recordings_fn``: the recorder's list (none without it)."""
        rec = self._component("recorder")
        return rec.recordings() if rec is not None else []

    def _recordings_folder(self) -> Any:
        return getattr(self._component("recorder"), "folder", None)

    def _child_pids(self) -> list:
        """mllink's ``child_pids``: show_service's other children counted in its CPU share (the owner's 0.8): the
        clock's beat process (None while it is down or not in use)."""
        fn = getattr(self.clock, "beat_pid", None)
        return [fn()] if fn is not None else []

    def _rig_kw(self, patch_path: Any) -> dict:
        """Stage 5 (README S5.4): the rig's inputs, all by reference. ``neutral`` is artnet_out's own colour
        (SHOW_PALETTE, else NEUTRAL_RGB: warm white at 2700 K) and hue depth, so a rig with no vibe looks exactly like the virtual Maestro."""
        d, f, c = self.director, self.features, self.clock
        amod = self._amod
        neutral = None
        rgb = getattr(amod, "NEUTRAL_RGB", None) or getattr(amod, "DEFAULT_RGB", None)   # artnet_out's instance neutral
        depth = getattr(amod, "HUE_DEPTH", None)
        if rgb is not None and depth is not None:
            neutral = (self._out_cfg.get("palette") or rgb, depth)
        driver_kw: dict[str, dict] = {"artnet": {"state_file": pathlib.Path(self.opts.state_dir) / "nodes.json"}}
        if patch_path:
            driver_kw["hue"] = {"patch_path": pathlib.Path(patch_path)}
        return {"palette_fn": getattr(d, "palette_fn", None), "look_fn": getattr(d, "look_fn", None),
                "clock": c if c is not None and hasattr(c, "snapshot") else None,
                "features_fn": f.latest if f is not None else None, "neutral": neutral,
                "latency_path": pathlib.Path(self.cfg.latency_file),
                **({"patch_path": pathlib.Path(patch_path)} if patch_path else {}), "driver_kw": driver_kw,
                "follow_file": pathlib.Path(self.opts.state_dir) / "follow.json"}       # README S5.23

    def _build_brain(self, patch_path: Any) -> None:
        """README S3.4 steps 3-5: the ring and the note tap (show-rx writers), the context, the Brain and its
        components. Each module is imported lazily: a missing one is in ``modules`` and left out."""
        rmod = self._import("ring")
        if rmod is not None:
            self.rings = self._make("ring", lambda: rmod.Rings())
        smod = self._import("situations")
        if smod is not None:
            self.tap = self._make("situations", lambda: smod.NoteTap())
        mlmod = self._import("mllink")                   # Stage 4: the worker's feed (MLLink joins the brain below)
        if mlmod is not None:
            self.mlfeed = self._make("mllink", lambda: mlmod.MLFeed())
        bmod = self._import("brain")
        if bmod is None:
            return
        f, c, h, rx = self.features, self.clock, self.harmony, self.rx
        ctx = contract.BrainContext(
            publish=self.publish,
            features=f.latest if f is not None else None,
            clock=c.snapshot if c is not None and hasattr(c, "snapshot") else None,
            harmony=(lambda: getattr(h, "latest", None)) if h is not None else None,
            piano=self._piano_view, health=self.health, rings=self.rings, notes=self.tap,
            clock_action=self._clock_action if c is not None else None,
            set_gate_ref=self._set_gate_ref if f is not None else None,
            set_senders=getattr(rx, "set_senders", None), set_priority=getattr(rx, "set_priority", None),
            static_senders=tuple(self.cfg.senders),
            notify=getattr(self.api, "notify", None),
            rhythm=getattr(f, "rhythm", None) if f is not None else None,
            ml_feed=self.mlfeed,
            clock_correct=getattr(c, "apply_integer_correction", None) if c is not None else None)
        self.ctx = ctx
        sd = pathlib.Path(self.opts.state_dir)
        self.brain = self._make("brain", lambda: bmod.Brain(ctx, show_file=pathlib.Path(self.opts.show_file),
                                                            state_file=sd / "state.json"))
        if self.brain is None:
            return
        for name in BRAIN_MODULES:
            mod = self._import(name)
            if mod is None:
                continue
            kw = self._component_kw(name, patch_path)
            got = self._make(name, lambda: mod.attach(ctx, **kw))
            if got is None:
                continue
            for comp in (got if isinstance(got, (list, tuple)) else [got]):
                try:
                    self.brain.add(comp)
                except Exception as e:
                    self.modules[name] = f"failed: {type(e).__name__}: {e}"
                    log.exception("%s could not join the brain: running without it", name)
            if name == "director" and not isinstance(got, (list, tuple)):
                self.director = got
            if name == "mllink" and not isinstance(got, (list, tuple)):
                self.ml = got
            if name == "outputs" and not isinstance(got, (list, tuple)):
                self.rig = got
            if name == "recorder" and callable(getattr(got, "on_midi", None)):
                self.recorder = got                      # on_midi's tap (show-rx), after NoteTap
        self._wire_sources()
        stage3 = ("api", "ring", "situations", "brain") + BRAIN_MODULES
        self.brain.modules = {k: v for k, v in self.modules.items() if k in stage3}

    def _wire_sources(self) -> None:
        """sources.py's runtime acoustic offset seam (a rendered test-piano run's lead): Show.set_runtime_offset; and
        its mic claim seam (a rendered run on Sonos claims the mic that hears that room, for its calibration):
        Mic.claim_stream as the "test" claim."""
        comps = getattr(self.brain, "components", None)
        src = comps.get("sources") if isinstance(comps, dict) else None
        fn = getattr(src, "set_offset_fn", None)
        if callable(fn):
            try:
                fn(self.set_runtime_offset)
            except Exception:
                log.exception("sources: the runtime offset seam wasn't wired: rendered test runs play without it")
        claim_fn = getattr(src, "set_mic_claim_fn", None)
        mic = self.mic
        if callable(claim_fn) and mic is not None and callable(getattr(mic, "claim_stream", None)):
            try:
                claim_fn(lambda stream, on: mic.claim_stream(stream, by="test", label="Test piano", on=on))
            except Exception:
                log.exception("sources: the mic claim seam wasn't wired: a test piano on Sonos is heard by its room's "
                              "mic only if something else turns it on")

    def _wire_mic(self) -> None:
        """README S5.16: the mic's brain-side inputs (each optional): the ring, Auto's live on/off (and its wake on a
        toggle), the AI listening switch (and its wake), the room view for the mode's label, the card's refresh."""
        mic = self.mic
        if mic is None:
            return
        try:
            if self.rings is not None:
                mic.set_rings(self.rings)
            comps = getattr(self.brain, "components", None) or {}
            ap = comps.get("autopilot") if isinstance(comps, dict) else None
            if callable(getattr(ap, "is_on", None)):
                mic.set_auto(ap.is_on)
            add = getattr(ap, "add_on_listener", None)
            if callable(add):
                add(mic.wake)
            lis = comps.get("listener") if isinstance(comps, dict) else None
            if lis is not None and callable(getattr(lis, "add_on_listener", None)):
                mic.set_listening(lambda: lis.on is True)    # owner, 21 Oct: AI listening on claims the mic
                lis.add_on_listener(mic.wake)
            ctx = self.ctx
            mic.set_room(lambda: ctx.room() if ctx is not None and ctx.room is not None else None)
            mic.set_notify(getattr(self.api, "notify", None))
        except Exception:
            log.exception("mic: brain wiring failed: the mode claim alone decides")

    def _set_look_out(self, **kw: Any) -> None:
        """``ctx.set_look_out`` (Stage 5): Art-Net out's set_look, then the rig's (the look engine's), each under its
        own guard, so one failing never stops the other."""
        vm = getattr(self.artnet, "set_look", None)
        vkw = {k: v for k, v in kw.items() if k in VM_LOOK_KEYS}     # beat_depth is the rig's alone
        if vm is not None and vkw:
            try:
                vm(**vkw)
            except Exception:
                self._g["artnet.set_look"].failed()
        rs = getattr(self.rig, "set_look", None)
        rkw = {k: v for k, v in kw.items() if k in RIG_LOOK_KEYS}    # the plan is artnet_out's alone (README S5.18)
        if rs is not None and rkw:
            try:
                rs(**rkw)
            except Exception:
                self._g["rig.set_look"].failed()

    def _clock_action(self, word: str, t_s: float) -> None:
        """contract.ClockAction (any thread; the clock queues it for show-rx)."""
        c = self.clock
        if word == "tap":
            c.tap(t_s)
        elif word == "half":
            c.half()
        elif word == "double":
            c.double()
        elif word == "one":
            c.one(t_s)
        else:
            raise ValueError(f"unknown clock action {word!r}")

    def _set_gate_ref(self, lufs: float) -> None:
        """The vibe's loudness reference (§6.3) into Features (any thread: one attribute, read per hop)."""
        v = float(lufs)
        if math.isfinite(v):
            self.features.gate_ref_lufs = v

    def _piano_view(self) -> contract.PianoView:
        return self._pview

    def _state_channels(self) -> Optional[tuple[int, ...]]:
        """The MIDI channels a sender's state message describes (midi.Piano's ``state_channels``): show.yaml's
        ``situations.midi.channels`` (the keys' channel; a player's own playback channel stays held), read when a MIDI
        stream first appears (show-rx: a dict read, no I/O). Unset: every channel (midi.Piano's own default)."""
        settings = self.ctx.settings if self.ctx is not None else self._static_settings
        sec = ((settings or {}).get("situations") or {}).get("midi") if isinstance(settings, dict) else None
        ch = sec.get("channels") if isinstance(sec, dict) else None
        if isinstance(ch, list):
            got = tuple(int(c) for c in ch if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 16)
            return got or None
        return None

    def _engine(self) -> dict:
        """api.py's engine_ref (show-bus): what the card reads from the engine side."""
        h = self.health()
        f = None
        if self.features is not None:
            try:
                f = self.features.latest()
            except Exception:
                f = None
        feats = None
        if isinstance(f, dict):
            t = f.get("t_ms")
            feats = {"energy": f.get("energy"), "stream": f.get("stream"),
                     "age_ms": round(time.monotonic() * 1000.0 - t, 1) if isinstance(t, (int, float)) else None}
        clock = None
        if self.clock is not None and hasattr(self.clock, "snapshot"):
            try:
                clock = self.clock.snapshot()
            except Exception:
                clock = None
        return {"active_audio": h.get("active_audio"), "features": feats, "clock": clock, "inputs": _inputs(h),
                "input": _input(h), "mic": self.inputs.mic(h),
                "mic_state": self.mic.state() if self.mic is not None else None}

    def stop(self) -> None:
        if self.mic is not None:
            try:
                self.mic.stop()
            except Exception:
                log.exception("mic stop failed")
        if self.brain is not None:
            try:
                self.brain.stop()            # stops the test children and writes state.json
            except Exception:
                log.exception("brain stop failed")
        if self.artnet is not None:
            try:
                self.artnet.stop()
            except Exception:
                log.exception("Art-Net out stop failed")
        if self.rx is not None:
            try:
                self.rx.stop()
            except Exception:
                log.exception("receiver stop failed")
        if self.clock is not None and hasattr(self.clock, "stop"):
            try:
                self.clock.stop()            # show-beat (Stage 4), after show-rx: nothing puts hops any more
            except Exception:
                log.exception("clock stop failed")
        self.bus.stop()

    def set_hue_lead(self) -> None:
        """The virtual Maestro's Hue lead (README S5.25), at start and on every latency.json change: the rise onset
        fixtures.resolve_latency gives output "hue" (latency.json outputs.hue.intensity, from hue_cal.py; show.yaml
        looks.align_pct), else SHOW_HUE_LATENCY_MS, else 0."""
        set_ms = getattr(self.artnet, "set_hue_latency_ms", None)
        fmod = self._import("fixtures") if set_ms is not None else None
        if fmod is None:
            return
        try:
            import json
            import yaml
            path = pathlib.Path(self.cfg.latency_file)
            doc = json.loads(path.read_text()) if path.exists() else None    # none yet: the hand value
            show = yaml.safe_load(pathlib.Path(self.opts.show_file).read_text()) or {}
            align = (show.get("looks") or {}).get("align_pct", fmod.ALIGN_PCT)
            hand = {"intensity": {"rise_ms": self._out_cfg.get("hue_latency_ms") or 0.0}}
            lv = fmod.resolve_latency("hue", {"latency_key": "hue", "latency": hand}, doc, align_pct=float(align))
            set_ms(lv["rise_ms"])
            src = lv["source"]["rise"]
            log.info("Hue lead %.1f ms (%s)", lv["rise_ms"], "SHOW_HUE_LATENCY_MS" if src == "fixtures.yaml" else src)
        except Exception as e:
            log.warning("Hue lead not updated (%s): keeping %.1f ms", e,
                        getattr(self.artnet, "hue_latency_s", 0.0) * 1000)

    def set_gate_offsets(self, offsets: dict[str, float]) -> None:
        """Each stream's loudness-gate offset (its mic's calibration, config.GateOffsetWatch over audio_cal.json) to
        Features, swapped in by reference (main thread; show-rx reads it)."""
        if self.features is not None:
            self.features.gate_offsets = dict(offsets)

    def set_offsets(self, offsets: dict[str, float]) -> None:
        """latency.json's acoustic offsets (main thread); the runtime ones (``set_runtime_offset``) win over them."""
        with self._offsets_lock:
            self.offsets = dict(offsets)
            merged = {**self.offsets, **self._runtime_offsets}
            if self.rx is not None:
                try:
                    self.rx.set_offsets(merged)
                except Exception:
                    log.exception("set_offsets failed")

    def set_runtime_offset(self, stream: str, ms: Optional[float]) -> None:
        """A brain component's acoustic offset for one stream, merged over latency.json's (sources.py: the test
        piano's lead while a rendered run plays; any thread); None removes it (latency.json's value is back)."""
        with self._offsets_lock:
            if ms is None:
                self._runtime_offsets.pop(str(stream), None)
            else:
                self._runtime_offsets[str(stream)] = float(ms)
            merged = {**self.offsets, **self._runtime_offsets}
            if self.rx is not None:
                try:
                    self.rx.set_offsets(merged)
                except Exception:
                    log.exception("set_runtime_offset failed")

    # -- show-rx callbacks (the hot thread: no I/O, no locks) ---------------------------------------------------
    def on_audio(self, stream: str, n0: int, t0_host_s: float, pcm: Any, fs: int, flags: int) -> None:
        """contract.OnAudio: the active stream's block to Features, then the same block to the Clock, then (Stage 3)
        a 16 kHz mono copy into the listener's ring, then (Stage 4) the ML worker's feed."""
        t_in = time.perf_counter()
        f = self.features
        if f is not None:
            try:
                f.process(stream, n0, t0_host_s, pcm, fs, flags)
            except Exception:
                self._g["features.process"].failed()
        c = self.clock
        if c is not None:
            try:
                if f is not None and hasattr(c, "set_level"):         # a quiet room: no beat from talk or noise
                    lf = f.latest()
                    c.set_level(lf.get("gate") if isinstance(lf, dict) else None, t0_host_s)
                c.process(stream, n0, t0_host_s, pcm, fs, flags)
            except Exception:
                self._g["clock.process"].failed()
        r = self.rings
        if r is not None:
            try:
                r.write(stream, n0, t0_host_s, pcm, fs, flags)
            except Exception:
                self._g["ring.write"].failed()
        m = self.mlfeed
        if m is not None:
            try:
                m.write(stream, n0, t0_host_s, pcm, fs, flags)     # returns at once while no worker is attached
            except Exception:
                self._g["ml.feed"].failed()
        self._rx_audio_ms.append((time.perf_counter() - t_in) * 1000.0)

    def _features_publish(self, msg: dict) -> None:
        """The publish Features gets: everything to the bus; music.hit bodies also to Clock.observe_hit and (Stage 5)
        Rig.on_hit (show-rx)."""
        if msg.get("type") == contract.MUSIC_HIT:
            if self.clock is not None:
                try:
                    d = msg["data"]
                    self.clock.observe_hit(d["t_ms"] / 1000.0, d["band"], d["strength"])
                except Exception:
                    self._g["clock.observe_hit"].failed()
            rig = self.rig
            if rig is not None:                  # Stage 5: the hit flashes (DMX only) and pulls a frame forward
                try:
                    rig.on_hit(msg["data"])
                except Exception:
                    self._g["rig.hit"].failed()
        self.publish(msg)

    def on_midi(self, stream: str, msg: Any, arrival_s: float, to_host: Callable[[int], float]) -> None:
        tap = self.tap
        if tap is not None:                  # Auto's note-ons (situations), before the piano
            try:
                tap.on_midi(stream, msg, arrival_s, to_host)
            except Exception:
                self._g["notetap"].failed()
        rec = self.recorder
        if rec is not None:                  # the Room page's Record (recorder.py): one append while a take runs
            try:
                rec.on_midi(stream, msg, arrival_s, to_host)
            except Exception:
                self._g["recorder"].failed()
        live = self.piano_live
        if live is not None:                 # devices playing the piano (piano_live.py): a queue put while any listen
            try:
                live.on_midi(stream, msg, arrival_s, to_host)
            except Exception:
                self._g["piano_live"].failed()
        piano = self.pianos.get(stream)
        if piano is None:
            if self._midi_mod is None:
                return
            try:
                piano = self._midi_mod.Piano(stream, self.publish, state_channels=self._state_channels())
                _set_voice(piano, False, arrival_s)          # muted until it is the voice (on_tick / below)
            except Exception:
                self._g["on_midi"].failed()
                return
            self.pianos[stream] = piano
        try:
            piano.on_midi(msg, arrival_s, to_host)
            # the packet that makes a live piano present takes the voice at once (its snapshot carries the keys it
            # struck, at their own t_on_ms), rather than at the next tick
            if piano is not self._voice and _present_at(piano, arrival_s) and self._outranks(stream, piano):
                self._give_voice(piano, arrival_s)
        except Exception:
            self._g["on_midi"].failed()

    def _present_piano(self) -> Any:
        best = None
        for stream, p in self.pianos.items():
            if getattr(p, "present", False):
                if not _is_test(stream):
                    return p                 # live before test
                best = best or p
        return best

    def _outranks(self, stream: str, piano: Any) -> bool:
        """Should ``piano`` (present now) take the voice from the current one? Yes unless the current voice is
        present and as good (live, or test against test)."""
        v = self._voice
        if v is None or not getattr(v, "present", False):
            return True
        return _is_test(getattr(v, "stream", "")) and not _is_test(stream)

    def _give_voice(self, piano: Any, now_s: float) -> None:
        old = self._voice
        self._voice = piano
        if old is not None:
            _set_voice(old, False, now_s)
        _set_voice(piano, True, now_s)

    def on_tick(self, now_s: float) -> None:
        t_in = time.perf_counter()
        g = self._g
        for p in self.pianos.values():
            try:
                p.tick(now_s)
            except Exception:
                g["piano.tick"].failed()
        # one voice on the bus: the present piano, live before test; with none present the last voice keeps it
        # (it has sent its flip frame and is quiet), so a piano coming back needs no hand-over
        if self.pianos:
            try:
                want = self._present_piano()
                if want is not None and want is not self._voice:
                    self._give_voice(want, now_s)
            except Exception:
                g["piano.tick"].failed()
        f = self.features
        if f is not None:
            try:
                f.tick(now_s)
            except Exception:
                g["features.tick"].failed()
        if self.harmony is not None:
            try:
                self.harmony.tick(now_s, self._present_piano(), f.chroma12() if f is not None else None)
            except Exception:
                g["harmony.tick"].failed()
        if self.clock is not None:
            try:
                self.clock.tick(now_s)
            except Exception:
                g["clock.tick"].failed()
        if now_s >= self._snap_due:
            self._snap_due = now_s + SNAPSHOT_S
            try:
                self._snap = self._snapshot(now_s)
            except Exception:
                g["snapshot"].failed()
            if self.ctx is not None:
                try:
                    self._pview = self._build_piano_view(now_s)
                except Exception:
                    g["piano.view"].failed()
        self._rx_tick_ms.append((time.perf_counter() - t_in) * 1000.0)

    def _build_piano_view(self, now_s: float) -> contract.PianoView:
        """README S3.4 "Engine-side slots": the voice piano, for the director (between_pieces, dynamics) and Auto
        (piano_idle). Built on show-rx every SNAPSHOT_S; strikes come from the NoteTap (every note-on, by arrival)."""
        v = self._voice
        strikes: list = []
        if self.tap is not None and v is not None:
            stream = getattr(v, "stream", None)
            strikes = [e for e in self.tap.raw_since(now_s - PIANO_RATE_WINDOW_S) if e[1] == stream]
        new = [e for e in strikes if e[0] > self._pv_last]
        self._pv_last = now_s
        if new:
            mean = sum(e[4] for e in new) / (127.0 * len(new))
            if self._vel_ema is None:
                self._vel_ema = mean
            else:
                dt = max(0.0, now_s - (self._vel_t or now_s)) or SNAPSHOT_S
                self._vel_ema += (mean - self._vel_ema) * (1.0 - math.exp(-dt / PIANO_VEL_TAU_S))
            self._vel_t = now_s
        if v is None:
            return {**NEUTRAL_PIANO_VIEW, "velocity": round(self._vel_ema or 0.0, 3)}   # type: ignore[typeddict-item]
        last_age = None
        mmod = self._midi_mod
        table = getattr(v, "table", None)
        if mmod is not None and table is not None and hasattr(mmod, "T_ON"):
            col = table[:, mmod.T_ON]
            finite = col[col == col]                     # drop NaN (never struck)
            if finite.size:
                last_age = round(max(0.0, now_s - float(finite.max())), 3)
        if strikes and (last_age is None or now_s - strikes[-1][0] < last_age):
            last_age = round(max(0.0, now_s - strikes[-1][0]), 3)
        held = v.held() if hasattr(v, "held") else ()
        pedals = getattr(v, "pedals", None) or {}
        return {"present": bool(getattr(v, "present", False)), "stream": getattr(v, "stream", None),
                "source": getattr(v, "source", None), "keys_down": int(len(held)),
                "damper": round(float(pedals.get("damper", 0.0)), 3), "last_note_age_s": last_age,
                "velocity": round(self._vel_ema or 0.0, 3),
                "notes_per_s": round(len(strikes) / PIANO_RATE_WINDOW_S, 3)}

    def _snapshot(self, now_s: float) -> dict:
        """Built on show-rx (it owns these objects), read by the API on show-bus."""
        pianos = {}
        for stream, p in self.pianos.items():
            pianos[stream] = {"present": bool(_attr(p, "present", False)), "voice": p is self._voice,
                              "sess": _attr(p, "sess"), "seq": _attr(p, "seq"),
                              "pedals": dict(_attr(p, "pedals", {}) or {}), "stats": _attr(p, "stats")}
        return {"t_ms": round(now_s * 1000.0, 1), "pianos": pianos,
                "features": _attr(self.features, "stats") if self.features is not None else None,
                "clock": _attr(self.clock, "state") if self.clock is not None else None}

    # -- read from show-bus ------------------------------------------------------------------------------------
    def health(self) -> dict:
        rx = self.rx
        if rx is None:
            return {}
        try:
            h = rx.health()
        except Exception:
            return {}
        return h if isinstance(h, dict) else {}

    def state(self) -> Optional[dict]:
        """GET /api/v1/state: what it hears, from snapshots only. None until start() is done (503)."""
        if not self.ready:
            return None
        h, snap, bus = self.health(), self._snap, self.bus
        pianos = snap.get("pianos") or {}
        feats = bus.latest(contract.MUSIC_FEATURES)
        harm = bus.latest(contract.MUSIC_HARMONY)
        now_ms = time.monotonic() * 1000.0
        fdata = feats.get("data") if isinstance(feats, dict) else None
        hdata = harm.get("data") if isinstance(harm, dict) else None
        features = None
        if isinstance(fdata, dict):
            features = {k: fdata.get(k) for k in ("t_ms", "stream", "energy", "gate", "lufs_m", "lufs_s", "peak_dbfs",
                                                  "rms_dbfs", "onset", "brightness", "noisiness", "punch", "drive")}
            features["levels"] = {k: (v or {}).get("level") for k, v in (fdata.get("bands") or {}).items()
                                  if isinstance(v, dict)}
            t = fdata.get("t_ms")
            features["age_ms"] = round(now_ms - t, 1) if isinstance(t, (int, float)) else None
        return {
            "service": "show_service", "version": __version__, "t_ms": round(now_ms, 1),
            "uptime_s": round(time.monotonic() - self.t0, 1),
            "modules": dict(self.modules),
            "config": {"senders": [{"name": s.name, "host": s.host, "port": s.port, "streams": list(s.streams)}
                                   for s in self.cfg.senders],
                       "vban_bind": list(self.cfg.vban_bind), "bus_bind": list(self.cfg.bus_bind),
                       "latency_file": str(self.cfg.latency_file), "offsets_ms": dict(self.offsets)},
            "active_audio": h.get("active_audio"),
            "streams": h.get("streams") or {},
            "socket": h.get("socket"),
            "senders": h.get("senders"),
            "piano": {"present": any(p.get("present") for p in pianos.values()),
                      "voice": next((s for s, p in pianos.items() if p.get("voice")), None), "streams": pianos},
            "clock": snap.get("clock"),
            "features": features,
            "features_stats": snap.get("features"),
            "harmony": hdata,
            "artnet": _attr(self.artnet, "status") if self.artnet is not None else {"state": "off"},
            "bus": bus.stats(),
            "errors": {k: g.errors for k, g in self._g.items() if g.errors},
            "rx_ms": {"on_audio": _pcts(self._rx_audio_ms), "on_tick": _pcts(self._rx_tick_ms)},
            "threads": {t.name: t.native_id for t in threading.enumerate()},   # /proc/<pid>/task/<tid>: per thread
            "brain": self._brain_state(),
            "api": _attr(self.api, "stats") if self.api is not None else None,
            "ml": _attr(self.ml, "snapshot") if self.ml is not None else None,
            "rig": _attr(self.rig, "snapshot") if self.rig is not None else None,
            "mic": None if self.mic is None else {   # README S5.16: the decision and what the receiver keeps off
                **self.mic.state(), "receiver": {k: h.get(k) for k in ("off", "off_held", "off_decided", "off_dropped",
                                                                       "off_ignored")}},
        }

    def _brain_state(self) -> Optional[dict]:
        """``Brain.snapshot()``, plus the ring's and the note tap's stats; None with --no-brain."""
        if not self.opts.brain:
            return None
        b = self.brain
        out: dict = {}
        if b is not None:
            try:
                out = dict(b.snapshot())
            except Exception as e:
                out = {"error": f"snapshot failed: {type(e).__name__}: {e}"}
        else:
            out = {"running": False, "modules": {k: v for k, v in self.modules.items()
                                                 if k in ("api", "ring", "situations", "brain") + BRAIN_MODULES}}
        out["ring"] = _attr(self.rings, "info") if self.rings is not None else None
        out["notes"] = _attr(self.tap, "stats") if self.tap is not None else None
        out["piano_view"] = self._pview
        return out


def main(argv: list[str] | None = None) -> int:
    opts = config.load_options(argv)
    cfg = opts.config
    logging.basicConfig(stream=sys.stderr, level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")
    for note in opts.notes:
        log.warning("config: %s", note)
    if cfg.stream_priority:
        log.info("audio streams ranked first: %s (then %s)", ",".join(cfg.stream_priority),
                 ",".join(contract.AUDIO_PRIORITY))
    if not cfg.senders:
        log.warning("no senders configured (SHOW_SENDERS / --sender): listening for push-only senders only")
    sys.setswitchinterval(0.001)

    stop = threading.Event()

    def _signal(signum: int, _frame: Any) -> None:
        log.info("signal %d: stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    if not opts.brain:
        log.info("the brain is off (--no-brain / SHOW_BRAIN=off): no director, Auto, test sources or listener")
    show = Show(opts)
    show.start(freeze_gc=True)
    watch = config.LatencyWatch(cfg.latency_file)
    offsets = watch.poll(force=True)
    show.set_offsets(offsets or {})
    show.set_hue_lead()
    if offsets:
        log.info("acoustic offsets from %s: %s", cfg.latency_file, offsets)
    gwatch = config.GateOffsetWatch(opts.audio_cal_file)
    gates = gwatch.poll(force=True)
    show.set_gate_offsets(gates or {})
    if gates:
        log.info("loudness gate offsets from %s (dB): %s", opts.audio_cal_file, gates)
    t_start, frozen, errors_seen = time.monotonic(), False, 0
    while not stop.wait(RELOAD_S):
        offsets = watch.poll()
        if offsets is not None:
            log.info("latency.json changed: acoustic offsets %s", offsets or "none (all 0)")
            show.set_offsets(offsets)
            show.set_hue_lead()
        gates = gwatch.poll()
        if gates is not None:
            log.info("audio_cal.json changed: loudness gate offsets (dB) %s", gates or "none (all 0)")
            show.set_gate_offsets(gates)
        if watch.errors != errors_seen:
            errors_seen = watch.errors
            if errors_seen == 1 or errors_seen % 30 == 0:
                log.warning("latency.json unreadable (%d polls): keeping the last offsets", errors_seen)
        if not frozen and time.monotonic() - t_start >= WARMUP_S:
            gc.freeze()                      # warm-up objects (first packets, madmom's caches) out of later
            frozen = True                    # collections. No collect here: show-rx is live (Show.start collected)
    show.stop()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
