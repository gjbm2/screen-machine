"""Scores of whole pieces for the Music page (owner, 23 Oct: "feed a midi that user has recorded (or selected from
Music tab) to our score generator and display in browser ... no animation, just a generated score, suitable for
scrolling or paginating").

The live score's own generator (stream_service/score/sheet.py: ScoreBuilder + the analysis worker, in simulated
time) writes the piece once, on demand, the first time its score is opened: about a quarter of the piece's length
(a 3.5 min piece took 53 s), one piece at a time box-wide, SCHED_IDLE at nice 19 on the host's last 4 CPUs.  What
it keeps is the MEI (text, ~60 KB a piece) in ~/.cache/screen-machine/score, named by the MIDI's hash and the
generator's version, so a recording or a changed generator is written again.  Pages are laid out by Verovio for the
asking screen's size (a short subprocess, ~0.6 s; the last few layouts are kept in memory).  A score nobody is
waiting for any more (no poll for ABANDON_S) is stopped.

  GET /api/v1/score/<piece>?w=&h=&staff=      {"state": "queued" (+ "ahead") | "making" (+ "progress" 0-1) |
                                               "ready" (+ "pages": count, "rev") | "failed" (+ "detail"),
                                               "piece", "title", "composer"}
                                              404 {"error": "unknown_piece"}: no such piece, or no file (a phrase)
  GET /api/v1/score/<piece>/page/<n>.svg?w=&h=&staff=&rev=   page n (1-based) as image/svg+xml (rev: the status's,
                                              so a score written again isn't a cached old page)
w x h is one page in css px (default 800 x 1100); staff is a staff's height in css px (default 36).
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import os
import pathlib
import shutil
import time
from typing import Any, Optional

log = logging.getLogger("show_service.score_sheet")

REPO = pathlib.Path(__file__).resolve().parents[1]
PYTHON = pathlib.Path(os.environ.get("SM_SCORE_PYTHON", pathlib.Path.home() / ".venvs" / "sm-stream" / "bin" / "python"))
CACHE = pathlib.Path(os.environ.get("SM_SCORE_CACHE", pathlib.Path.home() / ".cache" / "screen-machine" / "score"))
KEEP = 400                           # MEI files kept (oldest dropped): ~25 MB
ABANDON_S = 45.0                     # a queued or running score nobody has polled for this long is dropped
FAILED_S = 600.0                     # a failure is answered as such this long before it's tried again
VERSION_S = 30.0                     # the generator's version is asked again at most this often
LAYOUTS = 6                          # page layouts kept in memory
PIN_LAST = 4


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if x != x else min(hi, max(lo, x))


def _cmd(*args: str, idle: bool = True) -> list[str]:
    """The sheet command.  ``idle`` (writing a piece): SCHED_IDLE at nice 19 on the host's last PIN_LAST CPUs (nice
    only ranks within WSL); else (a layout, someone waiting on it) nice 10."""
    cmd = [str(PYTHON), "-m", "stream_service.score.sheet", *args]
    if not idle:
        return ["nice", "-n", "10"] + cmd
    n = os.cpu_count() or 0
    if shutil.which("taskset") and n > PIN_LAST:
        cmd = ["taskset", "-c", f"{n - PIN_LAST}-{n - 1}"] + cmd
    if shutil.which("chrt"):
        cmd = ["chrt", "-i", "0"] + cmd
    return ["nice", "-n", "19"] + cmd


class _Job:
    def __init__(self, key: str, midi: pathlib.Path, title: str, composer: str) -> None:
        self.key, self.midi, self.title, self.composer = key, midi, title, composer
        self.progress = 0.0
        self.running = False
        self.polled = time.monotonic()
        self.proc: Optional[asyncio.subprocess.Process] = None


class ScoreSheets:
    def __init__(self, library: Any, cache: pathlib.Path = CACHE) -> None:
        self.library = library
        self.cache = pathlib.Path(cache)
        self.jobs: "collections.OrderedDict[str, _Job]" = collections.OrderedDict()   # queued in order; first runs
        self.failed: dict[str, tuple[float, str]] = {}
        self.layouts: "collections.OrderedDict[tuple, list[str]]" = collections.OrderedDict()
        self._version: tuple[float, str] = (0.0, "")
        self._runner: Optional[asyncio.Task] = None
        self._layout_lock: Optional[asyncio.Lock] = None

    # -- what a piece's score is ---------------------------------------------------------------------------------
    async def version(self) -> str:
        t, v = self._version
        if v and time.monotonic() - t < VERSION_S:
            return v
        out, _err = await self._run([str(PYTHON), "-m", "stream_service.score.sheet", "version"], timeout=30)
        try:
            v = json.loads(out.strip().splitlines()[-1])["version"]
        except Exception:                                                   # noqa: BLE001
            v = v or "unknown"
        self._version = (time.monotonic(), v)
        return v

    async def _key(self, midi: pathlib.Path) -> str:
        data = await asyncio.to_thread(midi.read_bytes)
        return f"{hashlib.sha1(data).hexdigest()[:16]}-{await self.version()}"

    def _mei(self, key: str) -> pathlib.Path:
        return self.cache / f"{key}.mei"

    # -- the one maker -------------------------------------------------------------------------------------------
    async def _run(self, cmd: list[str], timeout: float) -> tuple[str, str]:
        p = await asyncio.create_subprocess_exec(*cmd, cwd=str(REPO), stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE,
                                                 env={**os.environ, "PYTHONPATH": str(REPO)})
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            raise
        return out.decode(errors="replace"), err.decode(errors="replace")

    def _kick(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.get_running_loop().create_task(self._run_jobs())

    async def _run_jobs(self) -> None:
        while self.jobs:
            now = time.monotonic()
            for k in [k for k, j in self.jobs.items() if not j.running and now - j.polled > ABANDON_S]:
                del self.jobs[k]                                            # nobody waiting
            if not self.jobs:
                return
            job = next(iter(self.jobs.values()))
            try:
                await self._make(job)
            except Exception as e:                                          # noqa: BLE001
                log.exception("score: %s failed", job.midi.name)
                self.failed[job.key] = (time.monotonic(), f"{type(e).__name__}: {e}")
            finally:
                self.jobs.pop(job.key, None)

    async def _make(self, job: _Job) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        out = self._mei(job.key)
        job.running = True
        args = ["make", str(job.midi), str(out)]
        if job.title:
            args += ["--title", job.title]
        if job.composer:
            args += ["--composer", job.composer]
        t0 = time.monotonic()
        p = job.proc = await asyncio.create_subprocess_exec(
            *_cmd(*args), cwd=str(REPO), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONPATH": str(REPO)}, start_new_session=True)
        last: dict = {}
        dropped = False
        try:
            assert p.stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(p.stdout.readline(), 5.0)
                except asyncio.TimeoutError:
                    line = None
                if line == b"":
                    break
                if time.monotonic() - job.polled > ABANDON_S:
                    log.info("score: %s dropped (nobody waiting)", job.midi.name)
                    dropped = True
                    p.kill()
                    break
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "progress" in msg:
                    job.progress = float(msg["progress"])
                else:
                    last = msg
        finally:
            if p.returncode is None:
                try:
                    p.kill()
                except ProcessLookupError:
                    pass
            await p.wait()
            job.proc = None
        if last.get("done") and out.exists():
            log.info("score: %s written, %s bars in %.0f s", job.midi.name, last.get("bars"), time.monotonic() - t0)
            self._prune()
        elif not dropped:
            self.failed[job.key] = (time.monotonic(), str(last.get("error") or f"the writer stopped ({p.returncode})"))

    def _prune(self) -> None:
        try:
            files = sorted(self.cache.glob("*.mei"), key=lambda f: f.stat().st_mtime)
            for f in files[:-KEEP]:
                f.unlink(missing_ok=True)
        except OSError:
            pass

    # -- the answers ---------------------------------------------------------------------------------------------
    def _piece(self, piece: str) -> tuple[pathlib.Path, dict]:
        it = self.library.item(piece) if self.library is not None else None
        midi = self.library.resolve(piece) if it is not None else None
        if midi is None:
            raise LookupError(piece)
        return midi, it

    async def status(self, piece: str, w: float, h: float, staff: float) -> dict:
        midi, it = self._piece(piece)
        title, composer = str(it.get("title") or ""), str(it.get("composer") or "")
        base = {"piece": piece, "title": title, "composer": composer or None}
        key = await self._key(midi)
        mei = self._mei(key)
        if mei.exists():
            pages = await self.pages(key, w, h, staff)
            return {**base, "state": "ready", "pages": len(pages), "rev": key}
        if key in self.failed and time.monotonic() - self.failed[key][0] < FAILED_S and key not in self.jobs:
            return {**base, "state": "failed", "detail": self.failed[key][1]}
        job = self.jobs.get(key)
        if job is None:
            self.failed.pop(key, None)
            job = self.jobs[key] = _Job(key, midi, title, composer)
            self._kick()
        job.polled = time.monotonic()
        if job.running:
            return {**base, "state": "making", "progress": round(job.progress, 3)}
        return {**base, "state": "queued", "ahead": list(self.jobs).index(key)}

    async def pages(self, key: str, w: float, h: float, staff: float) -> list[str]:
        lk = (key, round(w / 10) * 10, round(h / 10) * 10, round(staff))
        got = self.layouts.get(lk)
        if got is not None:
            self.layouts.move_to_end(lk)
            return got
        if self._layout_lock is None:
            self._layout_lock = asyncio.Lock()
        async with self._layout_lock:                                       # one layout at a time
            got = self.layouts.get(lk)
            if got is not None:
                return got
            out, err = await self._run(_cmd("pages", str(self._mei(key)), "--width", str(lk[1]), "--height",
                                            str(lk[2]), "--staff", str(lk[3]), idle=False), timeout=120)
            try:
                msg = json.loads(out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise RuntimeError(f"the layout gave nothing ({err.strip()[-200:]})") from None
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            got = list(msg["pages"])
            self.layouts[lk] = got
            while len(self.layouts) > LAYOUTS:
                self.layouts.popitem(last=False)
            return got

    # -- routes (show-bus) ---------------------------------------------------------------------------------------
    def routes(self, app: Any) -> None:
        r = app.router
        r.add_get("/api/v1/score/{id}", self._h_status)
        r.add_get("/api/v1/score/{id}/page/{n}.svg", self._h_page)

    @staticmethod
    def _size(q: Any) -> tuple[float, float, float]:
        return (_clamp(q.get("w"), 240, 4000, 800), _clamp(q.get("h"), 240, 6000, 1100),
                _clamp(q.get("staff"), 16, 120, 36))

    async def _h_status(self, request: Any) -> Any:
        from aiohttp import web
        try:
            body = await self.status(request.match_info["id"], *self._size(request.query))
        except LookupError:
            return web.json_response({"error": "unknown_piece", "detail": "That piece has no MIDI file to write."},
                                     status=404)
        except Exception as e:                                              # noqa: BLE001
            log.exception("score: status failed")
            return web.json_response({"error": "failed", "detail": f"{type(e).__name__}: {e}"}, status=500)
        return web.json_response(body, headers={"Cache-Control": "no-store"})

    async def _h_page(self, request: Any) -> Any:
        from aiohttp import web
        try:
            midi, _it = self._piece(request.match_info["id"])
            key = await self._key(midi)
            if not self._mei(key).exists():
                return web.json_response({"error": "not_ready", "detail": "The score isn't written yet."}, status=404)
            pages = await self.pages(key, *self._size(request.query))
            n = int(request.match_info["n"])
            if not 1 <= n <= len(pages):
                return web.json_response({"error": "no_page", "detail": "There's no such page."}, status=404)
        except LookupError:
            return web.json_response({"error": "unknown_piece", "detail": "That piece has no MIDI file to write."},
                                     status=404)
        except ValueError:
            return web.json_response({"error": "no_page", "detail": "There's no such page."}, status=404)
        except Exception as e:                                              # noqa: BLE001
            log.exception("score: page failed")
            return web.json_response({"error": "failed", "detail": f"{type(e).__name__}: {e}"}, status=500)
        return web.Response(text=pages[n - 1], content_type="image/svg+xml",
                            headers={"Cache-Control": "private, max-age=300"})
