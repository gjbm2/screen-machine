"""One rendered test-piano run for show_sender: render (cached) at idle CPU priority, then play it on PC22 on command.

    ~/.venvs/sm-piano/bin/python -m piano_render.session --midi PATH --instrument ID [--pad-s X]

Started by tools/show_sender/show_sender.py (``--test-piano PIECE --instrument ID --output pc22``) from the repo root.

stdout: the protocol only, one JSON object per line, flushed (anything else that writes to fd 1 goes to stderr; logs
are on stderr):
  {"ev": "progress", "stage": "rendering", "step", "done_s", "total_s", "frac"}
        while rendering, at most 4 a second. step: render.py's stage (load|engine|realism|master|write); done_s and
        total_s: that stage's; frac: the whole render, 0..1. Nothing when the render is cached.
  {"ev": "rendered", "path", "duration_s", "onset_ms", "cached", "render_s"}
        once; then it waits for commands. duration_s: the file's (sample 0 = MIDI time 0, tail included).
  {"ev": "started", "t_audio0", "pad_s", "startup_s"}
        after each "play": t_audio0 = the WSL time.monotonic() at which MIDI time 0 is heard (file sample 0 at PC22's
        DAC, after pad_s of digital silence), from piano_render.pc22.PC22Output.start.
  {"ev": "played", "underruns", "frames", ...}   the audio ended by itself (the player's "done")
  {"ev": "stopped"}                             after "stop": silent (also if nothing was playing)
  {"ev": "error", "why": plain words}           then exit 1
stdin: one command per line:
  play   start (or restart) PC22 playback of the rendered file, pad_s of silence first; a "play" before "rendered"
         waits for it
  stop   silence within ~50 ms (the player's 10 ms fade, then the device buffer); a render still running is killed
  EOF    stop, then exit 0. SIGTERM, SIGHUP and the parent's death (PR_SET_PDEATHSIG) do the same.

Priorities: the render runs in a child process of its own (this module with --render-child) at SCHED_IDLE + nice 19,
so the live TV streams aren't starved; it dies with this process. This process is the playback path (the PCM
writer thread and the player's pipe) and keeps normal priority: it leaves SCHED_IDLE if it inherited it (as far as
Linux lets an unprivileged process). pc22.py tracks its player and kills it at exit; every exit path here stops it.

PIANO_SESSION_GAIN_DB (env, dB): scales the PCM sent to the player (-120 = a silent end-to-end check of the real PC22
path). If pc22.py no longer sends its PCM through ``to_pcm``, a run with it set refuses to play (fails closed).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("piano_render.session")

REPO = Path(__file__).resolve().parent.parent
GAIN_ENV = "PIANO_SESSION_GAIN_DB"
PROGRESS_EVERY_S = 0.25
PAD_MAX_S = 30.0
POLL_S = 0.25
#: each render.py stage's share of the whole render (frac)
STEP_SPAN = {"start": (0.0, 0.0), "load": (0.0, 0.05), "engine": (0.05, 0.80), "realism": (0.80, 0.95),
             "master": (0.95, 0.98), "write": (0.98, 1.0)}
PR_SET_PDEATHSIG = 1


class Proto:
    """The protocol's stdout. Given no stream it keeps fd 1 for itself and points fd 1 (and sys.stdout) at stderr,
    so library chatter can never break a line."""

    def __init__(self, stream=None) -> None:
        if stream is None:
            stream = os.fdopen(os.dup(1), "w", encoding="utf-8")
            os.dup2(2, 1)
            sys.stdout = sys.stderr
        self.f = stream
        self.lock = threading.Lock()
        self.ok = True

    def emit(self, obj: dict) -> bool:
        line = json.dumps(obj, separators=(",", ":"), default=str)
        with self.lock:
            try:
                self.f.write(line + "\n")
                self.f.flush()
                return True
            except (OSError, ValueError):                 # the reader is gone
                self.ok = False
                return False


class _Term(BaseException):
    """SIGTERM/SIGHUP in the main thread: unwind through the finally blocks (they stop the sound)."""


def plain(e: BaseException) -> str:
    """An exception as plain words for the Room page."""
    if isinstance(e, KeyError):
        return str(e.args[0]) if e.args else "unknown name"
    if isinstance(e, FileNotFoundError):
        return f"file not found: {e.filename or e}"
    if isinstance(e, MemoryError):
        return "out of memory"
    text = " ".join(str(e).split())
    return text or type(e).__name__


def frac(state: dict) -> float:
    """render.py's progress state -> the whole render's fraction done (STEP_SPAN)."""
    lo, hi = STEP_SPAN.get(str(state.get("stage")), (0.0, 0.0))
    try:
        done, tot = float(state.get("done_s") or 0.0), float(state.get("total_s") or 0.0)
    except (TypeError, ValueError):
        done, tot = 0.0, 0.0
    x = min(1.0, max(0.0, done / tot)) if tot > 0 else 0.0
    return round(lo + (hi - lo) * x, 4)


def _pdeathsig(sig: int) -> None:
    """Ask Linux to send ``sig`` when the parent goes (best effort), and go now if it already has."""
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, int(sig), 0, 0, 0)
    except Exception:                                    # noqa: BLE001
        pass
    if os.getppid() == 1:
        os.kill(os.getpid(), sig)


def sched_state() -> dict:
    try:
        pol = os.sched_getscheduler(0)
        name = {getattr(os, "SCHED_IDLE", -1): "idle", getattr(os, "SCHED_OTHER", -2): "other",
                getattr(os, "SCHED_BATCH", -3): "batch"}.get(pol, str(pol))
    except (AttributeError, OSError):
        name = "unknown"
    try:
        nice = os.nice(0)
    except OSError:
        nice = None
    return {"sched": name, "nice": nice}


def leave_idle() -> None:
    """The playback path must not be SCHED_IDLE: leave it if inherited (allowed when RLIMIT_NICE permits)."""
    try:
        if os.sched_getscheduler(0) == os.SCHED_IDLE:
            os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
            log.info("left SCHED_IDLE for playback")
    except (AttributeError, OSError) as e:
        log.warning("playback runs at SCHED_IDLE (inherited; can't leave it: %s)", e)


# -- the render child ---------------------------------------------------------------------------------------------
def render_child(midi: str, instrument: str, proto: Proto, *, idle: bool = True,
                 render_fn: Optional[Callable[..., Any]] = None) -> int:
    """--render-child: render (or find in the cache) at SCHED_IDLE + nice 19; progress and the result as protocol
    lines on ``proto``. Returns the exit code."""
    if idle:
        from .__main__ import lower_priority
        lower_priority()
    pri = sched_state()
    t = time.monotonic()
    last = {"t": 0.0, "step": None}

    def progress(state: dict) -> None:
        now = time.monotonic()
        step = state.get("stage")
        if step == last["step"] and now - last["t"] < PROGRESS_EVERY_S:
            return
        last["t"], last["step"] = now, step
        proto.emit({"ev": "progress", "stage": "rendering", "step": step, "done_s": state.get("done_s"),
                    "total_s": state.get("total_s"), "frac": frac(state)})

    try:
        if render_fn is None:
            from . import render as _render
            render_fn = _render.render
        res = render_fn(midi, instrument, progress=progress)
    except Exception as e:                               # noqa: BLE001  one plain line, then exit 1
        log.exception("render %s on %s", midi, instrument)
        proto.emit({"ev": "error", "why": f"couldn't render {Path(midi).name} on {instrument}: {plain(e)}"})
        return 1
    proto.emit({"ev": "rendered", "path": str(res.path), "duration_s": float(res.duration_s),
                "onset_ms": float(res.onset_ms or 0.0), "cached": bool(getattr(res, "cached", False)),
                "render_s": round(time.monotonic() - t, 3), **pri})
    return 0


# -- the gain (PIANO_SESSION_GAIN_DB) -----------------------------------------------------------------------------
def apply_gain(pc22mod, gain_db: Optional[float]) -> Optional[float]:
    """Scale every block pc22.py sends to the player by gain_db (patches its ``to_pcm`` in this process). Raises
    RuntimeError when pc22.py no longer sends its PCM through to_pcm (never play unscaled when asked not to)."""
    if gain_db is None:
        return None
    code = getattr(getattr(getattr(pc22mod, "_Session", None), "_write", None), "__code__", None)
    if code is None or "to_pcm" not in code.co_names:
        raise RuntimeError(f"{GAIN_ENV} is set but pc22.py no longer sends its PCM through to_pcm: not playing")
    import numpy as np
    g = float(10.0 ** (float(gain_db) / 20.0))
    orig = getattr(pc22mod.to_pcm, "_unscaled", pc22mod.to_pcm)

    def to_pcm(block, fmt):
        return orig(np.asarray(block, dtype=np.float32) * np.float32(g), fmt)

    to_pcm._unscaled = orig                              # type: ignore[attr-defined]
    to_pcm.gain = g                                      # type: ignore[attr-defined]
    pc22mod.to_pcm = to_pcm
    log.info("%s=%g: the PCM sent to the player is scaled by %.3g", GAIN_ENV, gain_db, g)
    return g


def gain_from_env() -> Optional[float]:
    raw = os.environ.get(GAIN_ENV)
    if raw is None or not raw.strip():
        return None
    v = float(raw)                                       # ValueError: refused by main (fails closed)
    if v != v:
        raise ValueError(raw)
    return v


# -- the session ----------------------------------------------------------------------------------------------------
def _default_output():
    from . import pc22
    return pc22.PC22Output()


class Session:
    """The playback process: the render child, the commands, one PC22Output. run() returns the exit code."""

    def __init__(self, midi: str, instrument: str, pad_s: float = 0.0, *, proto: Proto,
                 output_factory: Optional[Callable[[], Any]] = None,
                 render_argv: Optional[list[str]] = None) -> None:
        self.midi, self.instrument = str(midi), str(instrument)
        self.pad_s = min(PAD_MAX_S, max(0.0, float(pad_s)))
        self.proto = proto
        self.output_factory = output_factory or _default_output
        self.render_argv = render_argv or [sys.executable, "-m", "piano_render.session", "--render-child",
                                           "--midi", self.midi, "--instrument", self.instrument]
        self.q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.rendered: Optional[dict] = None
        self.render_proc: Optional[subprocess.Popen] = None
        self.render_killed = False
        self.out: Any = None
        self.gen = 0                                     # bumped by every play and stop: a stale player is ignored
        self.pending_play = False

    # -- the render child --
    def _start_render(self) -> None:
        self.render_killed = False
        try:
            self.render_proc = subprocess.Popen(self.render_argv, cwd=str(REPO), stdin=subprocess.DEVNULL,
                                                stdout=subprocess.PIPE)
        except OSError as e:
            self.q.put(("render", {"ev": "error", "why": f"couldn't start the renderer: {plain(e)}"}))
            return
        threading.Thread(target=self._read_render, args=(self.render_proc,), name="render-out", daemon=True).start()

    def _read_render(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            try:
                msg = json.loads(line) if line else None
            except ValueError:
                log.warning("renderer: not a protocol line: %r", line[:200])
                continue
            if isinstance(msg, dict):
                self.q.put(("render", msg))
        self.q.put(("render_eof", proc.wait()))

    def _kill_render(self) -> None:
        p = self.render_proc
        if p is None or p.poll() is not None:
            return
        self.render_killed = True
        p.kill()
        try:
            p.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            log.error("renderer pid %d didn't exit after SIGKILL", p.pid)

    # -- commands --
    def _read_cmds(self, f) -> None:
        try:
            for line in iter(f.readline, ""):
                self.q.put(("cmd", line.strip()))
        except (OSError, ValueError):
            pass
        self.q.put(("eof", None))

    def _play(self) -> None:
        self.gen += 1
        if self.out is None:
            self.out = self.output_factory()
        threading.Thread(target=self._play_worker, args=(self.gen, self.rendered["path"]), name="play",
                         daemon=True).start()

    def _play_worker(self, gen: int, path: str) -> None:
        out = self.out
        t = time.monotonic()
        try:
            t0 = out.start(path, lead_in_ms=self.pad_s * 1000.0)
        except Exception as e:                           # noqa: BLE001  PC22Error and anything else
            self.q.put(("play_error", (gen, f"couldn't play on PC22: {plain(e)}")))
            return
        if gen != self.gen:                              # a stop (or another play) came during the start
            out.stop()
            return
        self.proto.emit({"ev": "started", "t_audio0": t0, "pad_s": self.pad_s,
                         "startup_s": round(time.monotonic() - t, 3)})
        out.wait()
        s = getattr(out, "session", None)
        self.q.put(("played", (gen, dict(getattr(out, "done", None) or {}) or None, getattr(s, "error", None))))

    def stop(self) -> None:
        """Silence (and a render still running is killed)."""
        self.gen += 1
        self.pending_play = False
        if self.rendered is None:
            self._kill_render()
        if self.out is not None:
            self.out.stop()

    def shutdown(self) -> None:
        self.gen += 1
        try:
            if self.out is not None:
                self.out.stop()
        finally:
            self._kill_render()

    def handle(self, kind: str, val: Any) -> Optional[int]:
        """One event; returns an exit code to end the run."""
        if kind == "cmd":
            if val == "play":
                if self.rendered is not None:
                    self._play()
                else:
                    self.pending_play = True
                    if self.render_proc is None or self.render_proc.poll() is not None:
                        self._start_render()               # stopped mid-render earlier: render again
            elif val == "stop":
                self.stop()
                self.proto.emit({"ev": "stopped"})
            elif val:
                log.warning("unknown command %r", val[:80])
        elif kind == "eof":
            return 0
        elif kind == "render":
            ev = val.get("ev")
            if ev == "progress":
                self.proto.emit(val)
            elif ev == "rendered":
                self.rendered = val
                log.info("rendered %s on %s: %s (%.1f s, %s, %.1f s)", Path(self.midi).name, self.instrument,
                         val.get("path"), float(val.get("duration_s") or 0.0),
                         "cached" if val.get("cached") else "new", float(val.get("render_s") or 0.0))
                self.proto.emit(val)
                if self.pending_play:
                    self.pending_play = False
                    self._play()
            elif ev == "error":
                self.proto.emit({"ev": "error", "why": str(val.get("why") or "the render failed")})
                return 1
        elif kind == "render_eof":
            if self.rendered is None and not self.render_killed:
                self.proto.emit({"ev": "error", "why": f"the renderer stopped (exit code {val}) before it finished"})
                return 1
        elif kind == "played":
            gen, done, err = val
            if gen == self.gen:
                if done is None and err:
                    self.proto.emit({"ev": "error", "why": f"PC22 playback stopped: {err}"})
                    return 1
                self.proto.emit({"ev": "played", **(done or {})})
        elif kind == "play_error":
            gen, why = val
            if gen == self.gen:
                self.proto.emit({"ev": "error", "why": why})
                return 1
        return None

    def run(self, cmds) -> int:
        threading.Thread(target=self._read_cmds, args=(cmds,), name="cmds", daemon=True).start()
        self._start_render()
        try:
            while True:
                try:
                    kind, val = self.q.get(timeout=POLL_S)
                except queue.Empty:
                    continue
                rc = self.handle(kind, val)
                if rc is not None:
                    return rc
        finally:
            self.shutdown()


# -- main -------------------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m piano_render.session", description=__doc__.split("\n")[0])
    ap.add_argument("--midi", required=True, help="the .mid to render and play")
    ap.add_argument("--instrument", required=True, help="instrument id (config/piano/instruments.json)")
    ap.add_argument("--pad-s", type=float, default=0.0, help="digital silence before MIDI time 0 on each play")
    ap.add_argument("--render-child", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s piano-session %(levelname)s %(message)s")
    proto = Proto()
    if a.render_child:
        _pdeathsig(signal.SIGKILL)
        return render_child(a.midi, a.instrument, proto)
    _pdeathsig(signal.SIGTERM)
    leave_idle()

    def _term(signum, _frame):
        raise _Term(signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _term)
    try:
        gain_db = gain_from_env()
    except ValueError:
        proto.emit({"ev": "error", "why": f"{GAIN_ENV}={os.environ.get(GAIN_ENV)!r} is not a number of dB"})
        return 1
    try:
        from . import pc22                               # now, while the render runs: the first play starts sooner
    except ImportError as e:
        proto.emit({"ev": "error", "why": f"PC22 playback can't load: {plain(e)}"})
        return 1
    if gain_db is not None:
        try:
            apply_gain(pc22, gain_db)
        except RuntimeError as e:
            proto.emit({"ev": "error", "why": str(e)})
            return 1
    s = Session(a.midi, a.instrument, a.pad_s, proto=proto)
    try:
        return s.run(sys.stdin)
    except _Term:
        return 0
    finally:
        s.shutdown()


if __name__ == "__main__":
    sys.exit(main())
