#!/usr/bin/env python3
"""Fast, exact replays of OUR clock's variants: the expensive parts of a run_stack.py replay are cached once per
track and condition, and each variant of show_service's Clock replays only the clock on top of them.

Why: a clock variant (alt/clockvar.py: a subclass of show_service.clock.Clock, never an edit of it) changes what the
clock does with madmom's activation, dsp's hits and the loudness gate, not those inputs. A full replay spends most of
its time computing them (madmom's two LSTMs about two thirds, dsp a sixth, the room model a few seconds a track), so
tuning a dozen variants on the dev subset under A and C would hold the shared CPU lock for hours. Instead:

1. **Record** (once per track and condition; heavy, under the CPU lock): run_stack.main itself, exactly as
   run_batch.py runs it (the same options, seeds and pre-roll), with three taps: the activation's every output and
   reset (``RecAct`` around the clock's own MadmomActivation), dsp's gate before each block and every ``music.hit``
   in the block or tick that published it, and the stream after the VBAN wire (INT16, so int16 on disk is exact).
   Its result JSON is the live path's own result (variant ``base``). Cache: <cache>/<dataset>__<id>/<key>.npz, the
   key hashing the audio and every run_stack option that changes the cached inputs (``cache_key``).
2. **Replay** (per variant; light): run_stack.main again with the decode, the room model, the wire and dsp replaced
   by the cache, and the activation by ``CachedAct`` (the recorded values, in order). Every block, tick, clock call
   and publish happens in the same order and host time as in the recording, so the stock clock gives identical
   flashes (checked by ``clockvar.py check``) and a variant's result differs only by what the variant does. The
   output is run_stack's own JSON (score.py scores it unchanged), plus ``candidate`` (the variant and its settings),
   a ``stack.id`` that includes the variant (score.py's NOTE catches mixed variants in a dir), ``trace`` (the clock's
   per-beat confidence terms, for diagnosis and the conf calibration) and ``clock_cpu`` (the clock's own time per
   audio second, madmom excluded; ``act_ms_per_s`` is madmom's own from the recording).
   A variant that resets the activation where the recording didn't (it changes the quiet gate or the segments) can't
   use the cached activation: ``CachedAct`` notices, and the replay refuses its result (``CacheMismatch``).
3. **Side lanes**, cached the same way and replayed at their live timing: ``ced`` (the ML worker's CED-mini on its
   1 s grid over the newest 2 s, alt/ced_lane.py in ~/.venvs/sm-show-ml) and ``beats`` (the slow lane's Beat This!
   pre-pass, run_stack's own ``slow_prepass`` on run_stack's own schedule).
4. **Other activations** (``--nets 8``): the clock's lane with a live MadmomActivation(nets=N) on the cached stream and
   dsp, recorded as ``act<N>`` (the resets fall where the 2-net recording's did: they follow dsp's gate and the
   segments, not the activation).

Nothing here edits show_service, stream_service, room_service or config; the run_stack module is patched in this
process only (``_Patch``), and every patch is undone after each track.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Optional

HERE = pathlib.Path(__file__).resolve().parent
BEAT_EVAL = HERE.parent
REPO = BEAT_EVAL.parents[2]
sys.path.insert(0, str(BEAT_EVAL))
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

import run_stack  # noqa: E402  (imports show_service: run this with ~/.venvs/sm-show)
from show_service import clock as clockmod  # noqa: E402
from show_service import contract, mllink  # noqa: E402

CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval/clockvar-cache").expanduser()
CPU_LOCK = pathlib.Path("~/.cache/screen-machine/cpu.lock").expanduser()
ML_PYTHON = pathlib.Path("~/.venvs/sm-show-ml/bin/python").expanduser()
CED_EVERY_S = 1.0           # show.yaml ml.tasks.ced / fast_lane: every second ...
CED_WINDOW_S = 2.0          # ... on the newest 2 s
CED_COST_S = 0.10           # a CED line reaches show_service this long after its grid point (22 ms a run unloaded,
                            # §6; the fast lane's cycle runs ced first; a line is read at once)


class CacheMismatch(RuntimeError):
    """The replayed clock asked the activation for other frames than the recording gave (another reset pattern)."""


@contextlib.contextmanager
def cpu_lock(on: bool = True):
    """flock ~/.cache/screen-machine/cpu.lock for one track's heavy step (other builders share it), at nice 19."""
    if not on:
        yield 0.0
        return
    CPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    t = time.time()
    with CPU_LOCK.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield time.time() - t
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class LockSlices:
    """The CPU lock in short slices over many tracks: ``with slices.track() as waited:`` takes the lock if it isn't
    held, and after the track lets it go once it has been held ``slice_s`` (so a slice is a few excerpts or one full
    track, then the other builders get a turn). ``close()`` lets it go."""

    def __init__(self, on: bool = True, slice_s: float = 45.0) -> None:
        self.on, self.slice_s = on, float(slice_s)
        self.f = None
        self.t_got = 0.0

    @contextlib.contextmanager
    def track(self):
        waited = 0.0
        if self.on and self.f is None:
            CPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
            t = time.time()
            self.f = CPU_LOCK.open("a")
            fcntl.flock(self.f, fcntl.LOCK_EX)
            self.t_got = time.time()
            waited = self.t_got - t
        try:
            yield waited
        finally:
            if self.f is not None and time.time() - self.t_got >= self.slice_s:
                self.close()

    def close(self) -> None:
        if self.f is not None:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()
            self.f = None


def nice19() -> None:
    try:
        os.nice(19 - os.nice(0))
    except OSError:
        pass


# -- the activation taps -----------------------------------------------------------------------------------------------
class RecAct:
    """Wraps the clock's activation: records every output (in call order) and every reset (the call count then), and
    times process_hop (madmom's own cost, ms per call)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name, self.lag_s, self.floor = inner.name, inner.lag_s, inner.floor
        self.nets = getattr(inner, "nets", None)
        self.vals: list[float] = []
        self.resets: list[int] = []
        self.cost_s = 0.0

    def process_hop(self, hop: np.ndarray) -> float:
        t = time.perf_counter()
        a = self.inner.process_hop(hop)
        self.cost_s += time.perf_counter() - t
        self.vals.append(float(a))
        return a

    def reset(self) -> None:
        self.resets.append(len(self.vals))
        self.inner.reset()


class CachedAct:
    """The recorded activation, replayed in call order. A reset must come where one came in the recording (repeated
    resets with no frame between are one: madmom resets lazily, at its next frame)."""

    def __init__(self, vals: np.ndarray, resets: np.ndarray, name: str = "madmom", lag_s: float = 0.020,
                 floor: float = 0.03) -> None:
        self.vals = np.asarray(vals, np.float32)
        self.want = sorted(set(int(r) for r in resets))
        self.got: list[int] = []
        self.name, self.lag_s, self.floor = name, float(lag_s), float(floor)
        self.i = 0
        self.mismatch: Optional[str] = None

    def process_hop(self, hop: np.ndarray) -> float:
        if self.i >= len(self.vals):
            self.mismatch = self.mismatch or f"frame {self.i} asked for, {len(self.vals)} recorded"
            return 0.0
        v = float(self.vals[self.i])
        self.i += 1
        return v

    def reset(self) -> None:
        if not self.got or self.got[-1] != self.i:
            self.got.append(self.i)

    def check(self) -> Optional[str]:
        if self.mismatch:
            return self.mismatch
        if self.i != len(self.vals):
            return f"{self.i} frames asked for, {len(self.vals)} recorded"
        if self.got != self.want:
            diff = sorted(set(self.got) ^ set(self.want))[:5]
            return f"resets differ from the recording (first: {diff})"
        return None


# -- recording ---------------------------------------------------------------------------------------------------------
class RecReplay(run_stack.Replay):
    """run_stack's Replay with the taps (module docstring, step 1)."""
    last: Optional["RecReplay"] = None

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.ev = 0
        self.gates: list[Optional[float]] = []
        self.block_ev: list[int] = []
        self.hit_rows: list[tuple[int, float, str, float]] = []
        self.feat_cache: list[list] = []
        self.clock.act = RecAct(self.clock.act)
        RecReplay.last = self

    def _features_pub(self, msg: dict) -> None:
        if msg.get("type") == contract.MUSIC_HIT:
            d = msg["data"]
            self.hit_rows.append((self.ev, d["t_ms"] / 1000.0, d["band"], float(d["strength"])))
        super()._features_pub(msg)

    def tick(self, h: float) -> None:
        self.ev += 1
        super().tick(h)

    def block(self, n0: int, blk: np.ndarray, flags: int, arrive: Optional[float] = None) -> None:
        # run_stack.Replay.block, with dsp's gate kept (the value Clock.set_level gets)
        self.ev += 1
        t0 = run_stack.T0 + n0 / run_stack.FS
        self.now = t0 + len(blk) / run_stack.FS + self.rx_latency_s if arrive is None else arrive
        pcm = blk.reshape(-1, 1)
        f = self.features
        try:
            f.process(run_stack.STREAM, n0, t0, pcm, run_stack.FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["features.process"] += 1
        gate = None
        try:
            lf = f.latest()
            gate = lf.get("gate") if isinstance(lf, dict) else None
            self.clock.set_level(gate, t0)
            self.clock.process(run_stack.STREAM, n0, t0, pcm, run_stack.FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["clock.process"] += 1
        self.gates.append(None if gate is None else float(gate))
        self.block_ev.append(self.ev)
        self.clock.wait_beat()

    def sample_features(self) -> None:
        lf = self.features.latest()
        ok = isinstance(lf, dict)
        self.feat_cache.append([self.now, lf.get("gate") if ok else None, lf.get("lufs_s") if ok else None,
                                lf.get("energy") if ok else None, 1.0 if ok else 0.0])
        super().sample_features()


class _Patch:
    """Set attributes on run_stack for one call, and put them back."""

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.old: dict = {}

    def __enter__(self) -> "_Patch":
        for k, v in self.kw.items():
            self.old[k] = getattr(run_stack, k)
            setattr(run_stack, k, v)
        return self

    def __exit__(self, *exc: Any) -> None:
        for k, v in self.old.items():
            setattr(run_stack, k, v)


# run_stack options that change what the cache holds (the stream, dsp's gate and hits, the activation); the others
# (--out, the slow lane's, --all-bodies, --beat-thread) only change what the clock does with it
_KEY_OPTS = {"--beats": 1, "--id": 1, "--dataset": 1, "--genre": 0, "--start-s": 1, "--dur-s": 1, "--gain-db": 1,
             "--degrade": 1, "--snr-db": 1, "--seed": 1, "--align-xcorr": 1, "--preroll-s": 1, "--rx-latency-ms": 1,
             "--loss-pct": 1, "--loss-burst": 1, "--gate-ref-lufs": 1}
_FLAGS = {"--align-xcorr", "--no-slow-lane", "--all-bodies"}


def cache_key(argv: list[str]) -> tuple[str, dict]:
    """(key, the options in it) for a run_stack argv: the audio and every option that changes the cached inputs."""
    import hashlib
    opts: dict = {"audio": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k, _, v = a.partition("=")
            if k in _FLAGS:
                val, i = True, i + 1
            elif v:
                val, i = v, i + 1
            else:
                val, i = (argv[i + 1] if i + 1 < len(argv) else None), i + 2
            if _KEY_OPTS.get(k):
                opts[k] = str(pathlib.Path(val).resolve()) if k in ("--degrade", "--beats") else val
        else:
            if opts["audio"] is None:
                opts["audio"] = str(pathlib.Path(a).resolve())
            i += 1
    return hashlib.sha256(json.dumps(opts, sort_keys=True).encode()).hexdigest()[:12], opts


def cache_path(argv: list[str]) -> pathlib.Path:
    key, opts = cache_key(argv)
    tid = opts.get("--id") or pathlib.Path(opts["audio"]).stem
    return CACHE / f"{safe(opts.get('--dataset') or '-')}__{safe(tid)}" / f"{key}.npz"


def safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def record(argv: list[str], cache_p: pathlib.Path) -> dict:
    """run_stack.main(argv) with the taps; writes its own result JSON (``--out`` in argv) and the cache."""
    cap: dict = {}
    real_wire, real_delay = run_stack.int16_wire, run_stack.estimate_delay

    def wire(x):
        y = real_wire(x)
        cap["y"] = y
        return y

    def delay(ref, y):
        cap["xc"] = real_delay(ref, y)
        return cap["xc"]

    real_decode = run_stack.decode

    def decode(path, start_s=0.0, dur_s=None):
        x = real_decode(path, start_s, dur_s)
        cap["x_shape"] = x.shape
        return x

    RecReplay.last = None
    with _Patch(Replay=RecReplay, int16_wire=wire, estimate_delay=delay, decode=decode):
        rc = run_stack.main(argv)
    rp = RecReplay.last
    if rc != 0 or rp is None:
        raise RuntimeError(f"run_stack failed (rc {rc})")
    act: RecAct = rp.clock.act                               # type: ignore[assignment]
    y = cap["y"]
    q = np.round(np.asarray(y, np.float64) * 32768.0)
    if not np.array_equal((q / 32768.0).astype(np.float32), np.asarray(y, np.float32)):
        raise RuntimeError("the wire's output is not INT16-exact: can't cache it as int16")
    hits = rp.hit_rows
    meta = {"x_shape": list(cap["x_shape"]), "xc": cap.get("xc"), "n_blocks": len(rp.gates),
            "act_name": act.name, "act_lag_s": act.lag_s, "act_floor": act.floor, "act_nets": act.nets,
            "act_frames": len(act.vals), "act_ms_per_s": round(act.cost_s * 1000.0 / (len(y) / run_stack.FS), 3),
            "features_stats": rp.features.stats(), "argv": argv, "stack": run_stack.stack_fingerprint(),
            "recorded": time.strftime("%Y-%m-%dT%H:%M:%S")}
    cache_p.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_p.with_name(cache_p.stem + ".tmp.npz")
    np.savez(tmp, y=np.clip(q, -32768, 32767).astype(np.int16),
             gates=np.array([np.nan if g is None else g for g in rp.gates], np.float64),
             block_ev=np.array(rp.block_ev, np.int64),
             hit_ev=np.array([h[0] for h in hits], np.int64), hit_t=np.array([h[1] for h in hits], np.float64),
             hit_band=np.array([h[2] for h in hits], dtype="U8"), hit_s=np.array([h[3] for h in hits], np.float64),
             feat=np.array([[np.nan if v is None else float(v) for v in r] for r in rp.feat_cache], np.float64)
             .reshape(-1, 5),
             act2=np.array(act.vals, np.float32), act2_resets=np.array(act.resets, np.int64),
             meta=np.array(json.dumps(meta, default=float)))
    tmp.replace(cache_p)
    return meta


class Cache:
    """One track's cache (``record``'s npz), plus its side lanes (``<stem>.ced.json``, ``<stem>.beats.json``,
    ``<stem>.act<N>.npz``) next to it."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        z = np.load(path, allow_pickle=False)
        self.z = {k: z[k] for k in z.files}
        self.meta = json.loads(str(self.z["meta"]))
        self._y: Optional[np.ndarray] = None

    @property
    def y(self) -> np.ndarray:
        if self._y is None:
            self._y = (self.z["y"].astype(np.float64) / 32768.0).astype(np.float32)
        return self._y

    def side(self, what: str) -> pathlib.Path:
        return self.path.with_name(self.path.stem + "." + what)

    def act(self, nets: int = 2) -> tuple[np.ndarray, np.ndarray]:
        if nets == 2:
            return self.z["act2"], self.z["act2_resets"]
        p = self.side(f"act{nets}.npz")
        if not p.exists():
            raise FileNotFoundError(f"{p}: record it first (clockvar.py record-act --nets {nets})")
        z = np.load(p)
        return z["vals"], z["resets"]

    def act_ms_per_s(self, nets: int = 2) -> Optional[float]:
        if nets == 2:
            return self.meta.get("act_ms_per_s")
        p = self.side(f"act{nets}.npz")
        if not p.exists():
            return None
        return json.loads(str(np.load(p)["meta"])).get("act_ms_per_s")

    def ced(self, norm: Optional[float] = None) -> Optional[list[dict]]:
        p = self.side(ced_name(norm))
        return json.loads(p.read_text())["lines"] if p.exists() else None

    def hits_by_ev(self) -> dict[int, list[tuple[float, str, float]]]:
        out: dict[int, list] = {}
        z = self.z
        for e, t, b, s in zip(z["hit_ev"].tolist(), z["hit_t"].tolist(), z["hit_band"].tolist(), z["hit_s"].tolist()):
            out.setdefault(int(e), []).append((float(t), str(b), float(s)))
        return out


# -- the fast replay ---------------------------------------------------------------------------------------------------
class _FeatStub:
    def __init__(self, stats: dict) -> None:
        self._stats = stats

    def stats(self) -> dict:
        return self._stats

    def latest(self) -> None:
        return None


class FastReplay(run_stack.Replay):
    """run_stack's Replay with dsp and the activation from the cache, and the clock class/kw of the variant
    (``FastReplay.setup`` before each track). Same call order and host times as the recording."""
    cfg: dict = {}
    last: Optional["FastReplay"] = None

    @classmethod
    def setup(cls, cache: Cache, clock_cls: type, clock_kw: dict, act_vals: np.ndarray, act_resets: np.ndarray,
              ced: Optional[list[dict]] = None, on_ml_line: Optional[Callable] = None,
              live_act: Any = None) -> None:
        cls.cfg = {"cache": cache, "clock_cls": clock_cls, "clock_kw": clock_kw, "act": (act_vals, act_resets),
                   "ced": ced, "on_ml_line": on_ml_line, "live_act": live_act}

    def __init__(self, *, beat_thread: bool, gate_ref_lufs: float, rx_latency_s: float, settings: dict,
                 all_bodies: bool = False) -> None:
        c = FastReplay.cfg
        cache: Cache = c["cache"]
        self.now = run_stack.T0
        self.all_bodies = all_bodies
        self.bodies: list = []
        self._last_key = None
        self.n_published = 0
        import collections
        self.hits = collections.Counter()
        self.feat_rows: list = []
        self.errors = collections.Counter()
        self.rx_latency_s = rx_latency_s
        m = cache.meta
        if c["live_act"] is not None:
            self.act = c["live_act"]
        else:
            self.act = CachedAct(*c["act"], name=m["act_name"], lag_s=m["act_lag_s"], floor=m["act_floor"])
        self.clock = c["clock_cls"](publish=self._clock_pub, activation=self.act, **c["clock_kw"])
        self.features = _FeatStub(m["features_stats"])
        ctx = contract.BrainContext(publish=lambda msg: None, clock=self.clock.snapshot,
                                    clock_correct=self.clock.apply_integer_correction)
        self.link = mllink.MLLink(ctx, mono=lambda: self.now, log_path=pathlib.Path(os.devnull))
        self.link.configure(settings)
        self.votes: list = []
        self.ev = 0
        self.bi = 0
        self._gates = cache.z["gates"]
        self._hits = cache.hits_by_ev()
        self._feat = cache.z["feat"]
        self._fi = 0
        self._ced = list(c["ced"] or [])
        self._ci = 0
        self.on_ml_line = c["on_ml_line"]
        self.clock_s = 0.0
        FastReplay.last = self

    def _inject_hits(self) -> None:
        for t, band, s in self._hits.get(self.ev, ()):
            self.hits[band] += 1
            try:
                self.clock.observe_hit(t, band, s)
            except Exception:                                # noqa: BLE001
                self.errors["clock.observe_hit"] += 1

    def _inject_ced(self, h: float) -> None:
        """CED lines whose arrival time has come (before this tick or block): the clock's music/speech word."""
        f = getattr(self.clock, "observe_ced", None)
        while self._ci < len(self._ced) and self._ced[self._ci]["arrive"] <= h:
            line = self._ced[self._ci]
            self._ci += 1
            if f is not None:
                f(line)

    def tick(self, h: float) -> None:
        self.ev += 1
        self.now = h
        self._inject_ced(h)
        self._inject_hits()
        t = time.perf_counter()
        try:
            self.clock.tick(h)
        except Exception:                                    # noqa: BLE001
            self.errors["clock.tick"] += 1
        self.clock_s += time.perf_counter() - t

    def block(self, n0: int, blk: np.ndarray, flags: int, arrive: Optional[float] = None) -> None:
        self.ev += 1
        t0 = run_stack.T0 + n0 / run_stack.FS
        self.now = t0 + len(blk) / run_stack.FS + self.rx_latency_s if arrive is None else arrive
        self._inject_ced(self.now)
        self._inject_hits()
        g = float(self._gates[self.bi]) if self.bi < len(self._gates) else math.nan
        self.bi += 1
        pcm = blk.reshape(-1, 1)
        t = time.perf_counter()
        try:
            self.clock.set_level(None if math.isnan(g) else g, t0)
            self.clock.process(run_stack.STREAM, n0, t0, pcm, run_stack.FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["clock.process"] += 1
        self.clock_s += time.perf_counter() - t

    def ml_line(self, h: float, line: dict) -> None:
        if self.on_ml_line is not None:
            try:
                self.on_ml_line(self, h, line)
            except Exception:                                # noqa: BLE001
                self.errors["ml.hook"] += 1
        super().ml_line(h, line)

    def sample_features(self) -> None:
        if self._fi < len(self._feat):
            r = self._feat[self._fi]
            self._fi += 1
            c = self.clock
            if r[4] > 0:                                     # dsp had a features body then
                self.feat_rows.append([self.now, *(None if math.isnan(v) else float(v) for v in r[1:4]),
                                       round(float(getattr(c, "_agc_db", math.nan)), 2),
                                       bool(getattr(c, "_quiet", False))])


def bt_prepass(y: np.ndarray, runs: list[dict], ml: dict, beats_cfg: dict, norm: float) -> tuple[list[dict], dict]:
    """run_stack.slow_prepass through alt/bt_lane.py (each window's level normalised to ``norm`` dBFS first)."""
    if not runs:
        return [], {"load_s": 0.0}
    with tempfile.TemporaryDirectory(prefix="clockvar-bt-") as td:
        audio, spec_p, out_p = (pathlib.Path(td) / n for n in ("y.npy", "spec.json", "out.json"))
        np.save(audio, y.astype(np.float32))
        spec = {"audio": str(audio), "t0": run_stack.T0, "stream": run_stack.STREAM,
                "models_dir": str(pathlib.Path(ml.get("models_dir") or "~/.cache/screen-machine/models").expanduser()),
                "model": beats_cfg.get("model", "beat-this-small0"), "window_s": float(beats_cfg.get("window_s", 30)),
                "interior_margin_s": float(beats_cfg.get("interior_margin_s", 3)),
                "silence_dbfs": float(ml.get("silence_dbfs", -70)), "runs": runs, "norm_dbfs": float(norm)}
        spec_p.write_text(json.dumps(spec))
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="")
        p = subprocess.run([str(ML_PYTHON), "-I", str(HERE / "bt_lane.py"), str(spec_p), str(out_p)], cwd=str(REPO),
                           capture_output=True, text=True, env=env)
        if p.returncode != 0:
            raise RuntimeError(f"bt_lane.py failed:\n{p.stderr[-1500:]}")
        res = json.loads(out_p.read_text())
    return res["runs"], {"load_s": res.get("load_s"), "model": res.get("model"), "norm_dbfs": norm}


def replay(argv: list[str], cache: Cache, clock_cls: type, clock_kw: dict, *, nets: int = 2,
           candidate: Optional[dict] = None, ced: bool = False, on_ml_line: Optional[Callable] = None,
           beats_cache: bool = True, live_act: Any = None, trace_fn: Optional[Callable] = None,
           ced_norm: Optional[float] = None, bt_norm: Optional[float] = None,
           candidate_extra: Optional[dict] = None) -> dict:
    """run_stack.main(argv) on the cache with the variant's clock; returns the result (also written to --out)."""
    m = cache.meta
    shape = tuple(m["x_shape"])
    y = cache.y
    vals, resets = cache.act(nets) if live_act is None else (np.zeros(0, np.float32), np.zeros(0, np.int64))
    ced_lines = None
    if ced:
        ced_lines = cache.ced(ced_norm)
        if ced_lines is None:
            raise FileNotFoundError(f"{cache.side(ced_name(ced_norm))}: run the CED lane first")
    FastReplay.setup(cache, clock_cls, clock_kw, vals, resets, ced=ced_lines, on_ml_line=on_ml_line,
                     live_act=live_act)
    real_prepass = run_stack.slow_prepass if bt_norm is None else \
        (lambda yy, runs, ml, bc: bt_prepass(yy, runs, ml, bc, bt_norm))
    bpath = cache.side(beats_name(bt_norm))
    bstore = json.loads(bpath.read_text()) if (beats_cache and bpath.exists()) else {}

    def prepass(yy, runs, ml, beats_cfg):
        want = [r for r in runs if f"{r['a']}:{r['b']}" not in bstore.get("runs", {})]
        info = bstore.get("info", {"load_s": 0.0})
        if want:                                             # Beat This! on windows not computed yet (heavy)
            res, info = real_prepass(yy, want, ml, beats_cfg)
            bstore.setdefault("runs", {}).update({f"{r['a']}:{r['b']}": o for r, o in zip(want, res)})
            bstore["info"] = info
            if beats_cache:
                bpath.write_text(json.dumps(bstore, default=float))
        return [bstore["runs"][f"{r['a']}:{r['b']}"] for r in runs], info

    real_fp = run_stack.stack_fingerprint

    def fingerprint():
        fp = real_fp()
        if candidate is not None:
            import hashlib
            fp["candidate"] = candidate
            fp["base_id"] = fp["id"]
            fp["id"] = fp["id"] + "+" + hashlib.sha256(json.dumps(candidate, sort_keys=True, default=str)
                                                       .encode()).hexdigest()[:8]
        return fp

    FastReplay.last = None
    with _Patch(Replay=FastReplay, decode=lambda path, start_s=0.0, dur_s=None: np.zeros(shape, np.float32),
                degrade=lambda x, model, seed, snr: y, estimate_delay=lambda ref, yy: float(m.get("xc") or 0.0),
                int16_wire=lambda x: y, slow_prepass=prepass, stack_fingerprint=fingerprint):
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            rc = run_stack.main(argv)
    rp = FastReplay.last
    if rc != 0 or rp is None:
        raise RuntimeError(f"run_stack failed (rc {rc})")
    bad = rp.act.check() if isinstance(rp.act, CachedAct) else None
    out = pathlib.Path(argv[argv.index("--out") + 1])
    r = json.loads(out.read_text())
    if bad:
        out.unlink(missing_ok=True)
        raise CacheMismatch(bad)
    audio_s = len(y) / run_stack.FS
    r["candidate"] = {**(candidate or {}), **(candidate_extra or {})} if candidate is not None else None
    r["clock_cpu"] = {"clock_ms_per_s": round(rp.clock_s * 1000.0 / audio_s, 3),
                      "act_ms_per_s": cache.act_ms_per_s(nets) if live_act is None else None, "nets": nets}
    if trace_fn is not None:
        r.update(trace_fn(rp.clock, run_stack.T0 + float(r["preroll_s"]) + float(r["degrade"]["delay_s"]
                                                                                 if r.get("degrade") else 0.0)))
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(r, separators=(",", ":"), default=float))
    tmp.replace(out)
    return r


# -- the CED lane ------------------------------------------------------------------------------------------------------
def ced_name(norm: Optional[float]) -> str:
    return "ced.json" if norm is None else f"ced-n{-float(norm):g}.json"


def beats_name(norm: Optional[float]) -> str:
    return "beats.json" if norm is None else f"beats-n{-float(norm):g}.json"

def ced_schedule(n48: int, phase_s: float, rx_latency_s: float) -> list[dict]:
    """The fast lane's CED turns: grid point g (stream s), window [b - 2 s, b) in 16 kHz samples (the newest whole
    MLFeed frames heard by g), arriving at g + CED_COST_S; none before the segment holds 2 s."""
    fs16 = run_stack.FS_ML
    need = int(round(CED_WINDOW_S * fs16))
    n16 = int(n48 * fs16 / run_stack.FS)
    end_s = n48 / run_stack.FS + rx_latency_s
    runs, k, g = [], 0, phase_s
    while g <= end_s + 1e-9:
        heard = (g - rx_latency_s) * fs16
        b = min(int(heard // run_stack.ML_FRAME) * run_stack.ML_FRAME, n16 // run_stack.ML_FRAME * run_stack.ML_FRAME)
        if b >= need and (not runs or b > runs[-1]["b"]):
            runs.append({"k": k, "g": g, "a": b - need, "b": b})
        k += 1
        g = phase_s + k * CED_EVERY_S
    return runs


def ced_prepass(cache: Cache, seed: int, rx_latency_s: float = run_stack.RX_LATENCY_MS / 1000.0,
                norm: Optional[float] = None) -> list[dict]:
    """CED-mini on every fast-lane turn of the cached stream (alt/ced_lane.py in the worker's venv). Lines carry
    ``arrive`` (host s) and ``t_end`` (host s of the window's end) besides the worker's words (music_p ...)."""
    y = cache.y
    phase = float(np.random.default_rng(seed + 104729).uniform(0, CED_EVERY_S))
    runs = ced_schedule(len(y), phase, rx_latency_s)
    with tempfile.TemporaryDirectory(prefix="clockvar-ced-") as td:
        audio, spec_p, out_p = (pathlib.Path(td) / n for n in ("y.npy", "spec.json", "out.json"))
        np.save(audio, y)
        spec = {"audio": str(audio), "runs": runs, "silence_dbfs": -70.0, "norm_dbfs": norm,
                "models_dir": str(pathlib.Path("~/.cache/screen-machine/models").expanduser())}
        spec_p.write_text(json.dumps(spec))
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                   CUDA_VISIBLE_DEVICES="")
        p = subprocess.run([str(ML_PYTHON), "-I", str(HERE / "ced_lane.py"), str(spec_p), str(out_p)],
                           cwd=str(REPO), capture_output=True, text=True, env=env)
        if p.returncode != 0:
            raise RuntimeError(f"ced_lane.py failed:\n{p.stderr[-1500:]}")
        res = json.loads(out_p.read_text())
    lines = []
    for r, o in zip(runs, res["runs"]):
        lines.append({**o, "g": run_stack.T0 + r["g"], "arrive": run_stack.T0 + r["g"] + CED_COST_S,
                      "t_end": run_stack.T0 + r["b"] / run_stack.FS_ML})
    cache.side(ced_name(norm)).write_text(json.dumps({"phase_s": phase, "cost_s": CED_COST_S, "lines": lines,
                                                      "norm_dbfs": norm, "cost_ms_mean": res.get("cost_ms_mean")},
                                                     default=float))
    return lines


# -- other activations -------------------------------------------------------------------------------------------------
def record_act(argv: list[str], cache: Cache, nets: int) -> dict:
    """The stock clock's lane with a live MadmomActivation(nets) on the cached stream and dsp: its activation, as
    ``act<nets>.npz`` (module docstring, step 4). The result JSON written is the stock clock on that activation."""
    act = RecAct(clockmod.MadmomActivation(nets=nets))
    replay(argv, cache, clockmod.Clock, {}, live_act=act, candidate={"variant": f"base-nets{nets}"})
    want = sorted(set(cache.z["act2_resets"].tolist()))
    got = sorted(set(act.resets))
    if len(act.vals) != len(cache.z["act2"]) or got != want:
        raise CacheMismatch(f"nets {nets}: {len(act.vals)} frames / resets {got[:5]} vs {len(cache.z['act2'])} / "
                            f"{want[:5]}")
    audio_s = len(cache.y) / run_stack.FS
    meta = {"nets": nets, "act_ms_per_s": round(act.cost_s * 1000.0 / audio_s, 3),
            "recorded": time.strftime("%Y-%m-%dT%H:%M:%S")}
    p = cache.side(f"act{nets}.npz")
    tmp = p.with_name(p.stem + ".tmp.npz")
    np.savez(tmp, vals=np.array(act.vals, np.float32), resets=np.array(act.resets, np.int64),
             meta=np.array(json.dumps(meta)))
    tmp.replace(p)
    return meta
