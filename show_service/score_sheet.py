"""Scores of whole pieces for the Music page (owner, 23 Oct: "feed a midi that user has recorded (or selected from
Music tab) to our score generator and display in browser ... no animation, just a generated score, suitable for
scrolling or paginating"; then: "first pass can't take minutes to generate a score ... if there's a more
sophisticated neural analysis pass then user could request that on demand").

stream_service/score/sheet.py writes a piece in one of two passes:
* **quick**, the first time its score is opened: ~1-3 s (a sequenced file read off its own grid, a performance's
  beat tracked once).  Up to QUICK_AT_ONCE run together, nice 10; the first answer waits up to QUICK_WAIT_S for it,
  so the score usually comes back at once.
* **better** (kind=better), only when asked for: the live score's networks over the whole piece once, then the live
  ScoreBuilder; ~25 ms of CPU a note (seconds for a recording, a minute or two for a long piece).  One at a time
  box-wide, nice 19 on the host's last 4 CPUs (nice only ranks within WSL), dropped if nobody polls for ABANDON_S.
What it keeps is the MEI (text, ~60 KB a piece, KEEP at most) in ~/.cache/screen-machine/score, named by the MIDI's
hash, the generator's version and the pass, so a recording or a changed generator is written again.  Pages are laid
out by Verovio for the asking screen's size (~0.6 s; the last few kept in memory); a PDF is printed on demand
(never kept on disk).  ``make`` runs in sm-score-ml's python (numpy, torch), pages / pdf / version in sm-stream's
(Verovio, Playwright).

  GET /api/v1/score/<piece>?w=&h=&staff=&kind=quick|better[&retry=1]
        {"state": "queued" (+ "ahead") | "making" (+ "progress" 0-1) | "ready" (+ "pages": count, "rev") |
         "failed" (+ "detail"), "kind", "better": the better pass's state ("none" until asked for), "piece",
         "title", "composer"}; retry=1 forgets an earlier failure and tries again.
        404 {"error": "unknown_piece"}: no such piece, or no file (a phrase)
  GET /api/v1/score/<piece>/page/<n>.svg?w=&h=&staff=&kind=&rev=   page n (1-based) as image/svg+xml (rev: the
        status's, so a score written again isn't a cached old page)
  GET /api/v1/score/<piece>/pdf?paper=a4|a4-landscape|letter&kind=[&download=1]   the score printed to a PDF (A4
        portrait by default; headless Chromium, ~5-10 s), inline unless download
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
import re
import shutil
import time
from typing import Any, Optional
from urllib.parse import quote

log = logging.getLogger("show_service.score_sheet")

REPO = pathlib.Path(__file__).resolve().parents[1]
PYTHON = pathlib.Path(os.environ.get("SM_SCORE_PYTHON", pathlib.Path.home() / ".venvs" / "sm-stream" / "bin" / "python"))
MAKE_PYTHON = pathlib.Path(os.environ.get("SM_SCORE_MAKE_PYTHON",
                                          pathlib.Path.home() / ".venvs" / "sm-score-ml" / "bin" / "python"))
CACHE = pathlib.Path(os.environ.get("SM_SCORE_CACHE", pathlib.Path.home() / ".cache" / "screen-machine" / "score"))
KINDS = ("quick", "better")
KEEP = 400                           # MEI files kept (oldest dropped): ~25 MB
QUICK_AT_ONCE = 3
QUICK_WAIT_S = 6.0                   # the first answer for a quick score waits this long for it
ABANDON_S = 45.0                     # a queued or running better pass nobody has polled for this long is dropped
FAILED_S = 120.0                     # a failure is answered as such this long (or until retry=1)
VERSION_S = 30.0                     # the generator's version is asked again at most this often
LAYOUTS = 6                          # page layouts kept in memory
PDFS = 4                             # PDFs kept in memory (never on disk)
PAPERS = ("a4", "a4-landscape", "letter")
PIN_LAST = 4


def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if x != x else min(hi, max(lo, x))


def _cmd(*args: str, python: Optional[pathlib.Path] = None, heavy: bool = False) -> list[str]:
    """The sheet command: nice 10 (someone waiting on it), or ``heavy`` (the better pass): nice 19 on the host's
    last PIN_LAST CPUs, the screens' own analysis worker's footing."""
    cmd = [str(python or PYTHON), "-m", "stream_service.score.sheet", *args]
    if not heavy:
        return ["nice", "-n", "10"] + cmd
    n = os.cpu_count() or 0
    if shutil.which("taskset") and n > PIN_LAST:
        cmd = ["taskset", "-c", f"{n - PIN_LAST}-{n - 1}"] + cmd
    return ["nice", "-n", "19"] + cmd


class _Job:
    def __init__(self, key: str, kind: str, midi: pathlib.Path, title: str, composer: str) -> None:
        self.key, self.kind, self.midi, self.title, self.composer = key, kind, midi, title, composer
        self.progress = 0.0
        self.running = False
        self.polled = time.monotonic()
        self.done = asyncio.Event()


class ScoreSheets:
    def __init__(self, library: Any, cache: pathlib.Path = CACHE) -> None:
        self.library = library
        self.cache = pathlib.Path(cache)
        self.jobs: dict[str, _Job] = {}
        self.better: "collections.OrderedDict[str, _Job]" = collections.OrderedDict()   # queued; the first runs
        self.failed: dict[str, tuple[float, str]] = {}
        self.layouts: "collections.OrderedDict[tuple, list[str]]" = collections.OrderedDict()
        self._version: tuple[float, str] = (0.0, "")
        self._runner: Optional[asyncio.Task] = None
        self._quick: Optional[asyncio.Semaphore] = None
        self._layout_lock: Optional[asyncio.Lock] = None
        self.pdfs: "collections.OrderedDict[tuple, bytes]" = collections.OrderedDict()

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

    async def _key(self, midi: pathlib.Path, kind: str = "quick") -> str:
        data = await asyncio.to_thread(midi.read_bytes)
        return f"{hashlib.sha1(data).hexdigest()[:16]}-{await self.version()}-{kind}"

    def _mei(self, key: str) -> pathlib.Path:
        return self.cache / f"{key}.mei"

    # -- the makers ----------------------------------------------------------------------------------------------
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

    def _start(self, job: _Job) -> None:
        self.jobs[job.key] = job
        loop = asyncio.get_running_loop()
        if job.kind == "quick":
            loop.create_task(self._quick_job(job))
        else:
            self.better[job.key] = job
            if self._runner is None or self._runner.done():
                self._runner = loop.create_task(self._run_better())

    async def _quick_job(self, job: _Job) -> None:
        if self._quick is None:
            self._quick = asyncio.Semaphore(QUICK_AT_ONCE)
        try:
            async with self._quick:
                await self._make(job)
        except Exception as e:                                              # noqa: BLE001
            log.exception("score: %s failed", job.midi.name)
            self.failed[job.key] = (time.monotonic(), f"{type(e).__name__}: {e}")
        finally:
            self.jobs.pop(job.key, None)
            job.done.set()

    async def _run_better(self) -> None:
        while self.better:
            now = time.monotonic()
            for k in [k for k, j in self.better.items() if not j.running and now - j.polled > ABANDON_S]:
                self.better.pop(k)                                          # nobody waiting
                self.jobs.pop(k, None)
            if not self.better:
                return
            job = next(iter(self.better.values()))
            try:
                await self._make(job)
            except Exception as e:                                          # noqa: BLE001
                log.exception("score: %s failed", job.midi.name)
                self.failed[job.key] = (time.monotonic(), f"{type(e).__name__}: {e}")
            finally:
                self.better.pop(job.key, None)
                self.jobs.pop(job.key, None)
                job.done.set()

    async def _make(self, job: _Job) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        out = self._mei(job.key)
        job.running = True
        args = ["make", str(job.midi), str(out)]
        if job.kind == "better":
            args.append("--neural")
        if job.title:
            args += ["--title", job.title]
        if job.composer:
            args += ["--composer", job.composer]
        t0 = time.monotonic()
        p = await asyncio.create_subprocess_exec(
            *_cmd(*args, python=MAKE_PYTHON, heavy=job.kind == "better"), cwd=str(REPO),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(REPO)}, start_new_session=True)
        err_tail: collections.deque = collections.deque(maxlen=20)

        async def drain() -> None:
            assert p.stderr is not None
            async for ln in p.stderr:
                err_tail.append(ln.decode(errors="replace").rstrip())
        drainer = asyncio.get_running_loop().create_task(drain())
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
                if job.kind == "better" and time.monotonic() - job.polled > ABANDON_S:
                    log.info("score: %s better pass dropped (nobody waiting)", job.midi.name)
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
            drainer.cancel()
        if last.get("done") and out.exists():
            log.info("score: %s %s pass written (%s), %s bars in %.1f s", job.midi.name, job.kind, last.get("how"),
                     last.get("bars"), time.monotonic() - t0)
            self._prune()
        elif not dropped:
            why = last.get("error") or (err_tail[-1] if err_tail else f"the writer stopped ({p.returncode})")
            log.warning("score: %s %s pass failed: %s", job.midi.name, job.kind, why)
            self.failed[job.key] = (time.monotonic(), str(why))

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

    def _state(self, key: str) -> dict:
        """One pass's state, without starting it: none | queued | making | ready | failed."""
        if self._mei(key).exists():
            return {"state": "ready"}
        job = self.jobs.get(key)
        if job is not None:
            if job.running:
                return {"state": "making", "progress": round(job.progress, 3)}
            return {"state": "queued", "ahead": list(self.better).index(key) if key in self.better else 0}
        if key in self.failed and time.monotonic() - self.failed[key][0] < FAILED_S:
            return {"state": "failed", "detail": self.failed[key][1]}
        return {"state": "none"}

    async def status(self, piece: str, w: float, h: float, staff: float, kind: str = "quick",
                     retry: bool = False) -> dict:
        midi, it = self._piece(piece)
        title, composer = str(it.get("title") or ""), str(it.get("composer") or "")
        key = await self._key(midi, kind)
        bkey = key if kind == "better" else await self._key(midi, "better")
        if retry:
            self.failed.pop(key, None)
        st = self._state(key)
        if st["state"] == "none":
            job = _Job(key, kind, midi, title, composer)
            self.failed.pop(key, None)
            self._start(job)
            if kind == "quick":
                try:
                    await asyncio.wait_for(job.done.wait(), QUICK_WAIT_S)
                except asyncio.TimeoutError:
                    pass
            st = self._state(key)
        job = self.jobs.get(key)
        if job is not None:
            job.polled = time.monotonic()
        out = {"piece": piece, "title": title, "composer": composer or None, "editable": bool(it.get("editable")),
               "kind": kind, **st,
               "better": self._state(bkey)["state"]}
        if st["state"] == "ready":
            out["pages"] = len(await self.pages(key, w, h, staff))
            out["rev"] = key
        return out

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
                                            str(lk[2]), "--staff", str(lk[3])), timeout=120)
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

    async def pdf(self, key: str, paper: str, title: str, composer: str) -> bytes:
        """The score printed to a PDF on ``paper`` (sheet_pages.pdf: headless Chromium, ~5-10 s), kept in memory."""
        pk = (key, paper, title, composer)
        got = self.pdfs.get(pk)
        if got is not None:
            self.pdfs.move_to_end(pk)
            return got
        if self._layout_lock is None:
            self._layout_lock = asyncio.Lock()
        async with self._layout_lock:                                       # one at a time, with the layouts
            got = self.pdfs.get(pk)
            if got is not None:
                return got
            out = self.cache / f".{key}-{paper}-{os.getpid()}.pdf"
            args = ["pdf", str(self._mei(key)), str(out), "--paper", paper]
            if title:
                args += ["--title", title]
            if composer:
                args += ["--composer", composer]
            try:
                text, err = await self._run(_cmd(*args), timeout=180)
                try:
                    msg = json.loads(text.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    raise RuntimeError(f"the PDF gave nothing ({err.strip()[-200:]})") from None
                if msg.get("error"):
                    raise RuntimeError(msg["error"])
                got = await asyncio.to_thread(out.read_bytes)
            finally:
                out.unlink(missing_ok=True)
            self.pdfs[pk] = got
            while len(self.pdfs) > PDFS:
                self.pdfs.popitem(last=False)
            return got

    # -- routes (show-bus) ---------------------------------------------------------------------------------------
    def routes(self, app: Any) -> None:
        r = app.router
        r.add_get("/api/v1/score/{id}", self._h_status)
        r.add_get("/api/v1/score/{id}/page/{n}.svg", self._h_page)
        r.add_get("/api/v1/score/{id}/pdf", self._h_pdf)

    @staticmethod
    def _kind(q: Any) -> str:
        k = q.get("kind") or "quick"
        return k if k in KINDS else "quick"

    @staticmethod
    def _size(q: Any) -> tuple[float, float, float]:
        return (_clamp(q.get("w"), 240, 4000, 800), _clamp(q.get("h"), 240, 6000, 1100),
                _clamp(q.get("staff"), 16, 120, 36))

    async def _h_status(self, request: Any) -> Any:
        from aiohttp import web
        try:
            body = await self.status(request.match_info["id"], *self._size(request.query), kind=self._kind(request.query),
                                     retry=bool(request.query.get("retry")))
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
            key = await self._key(midi, self._kind(request.query))
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

    async def _h_pdf(self, request: Any) -> Any:
        from aiohttp import web
        paper = request.query.get("paper") or "a4"
        if paper not in PAPERS:
            return web.json_response({"error": "bad_paper", "detail": f"Paper is one of {', '.join(PAPERS)}."},
                                     status=400)
        try:
            midi, it = self._piece(request.match_info["id"])
            key = await self._key(midi, self._kind(request.query))
            if not self._mei(key).exists():
                return web.json_response({"error": "not_ready", "detail": "The score isn't written yet."}, status=404)
            title, composer = str(it.get("title") or ""), str(it.get("composer") or "")
            data = await self.pdf(key, paper, title, composer)
        except LookupError:
            return web.json_response({"error": "unknown_piece", "detail": "That piece has no MIDI file to write."},
                                     status=404)
        except Exception as e:                                              # noqa: BLE001
            log.exception("score: pdf failed")
            return web.json_response({"error": "failed", "detail": f"{type(e).__name__}: {e}"}, status=500)
        name = re.sub(r"[^\w .,()'&-]+", "", title).strip() or "Score"
        ascii_name = name.encode("ascii", "ignore").decode().strip() or "Score"
        how = "attachment" if request.query.get("download") else "inline"
        return web.Response(body=data, content_type="application/pdf", headers={
            "Content-Disposition": f'{how}; filename="{ascii_name}.pdf"; filename*=UTF-8\'\'{quote(name)}.pdf',
            "Cache-Control": "private, max-age=300"})
