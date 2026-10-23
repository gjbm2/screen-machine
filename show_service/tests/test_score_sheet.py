"""score_sheet.py: a piece's quick score comes back in the first answer, the better pass only when asked for (queued,
making with progress, ready), pages laid out per size, a PDF printed, unknown pieces and failures answered, retry
tries again; the writer itself (stream_service/score/sheet.py) is faked."""
from __future__ import annotations

import asyncio
import pathlib
import stat
import sys

from show_service import score_sheet

FAKE = '''#!{py}
import json, pathlib, sys, time
a = sys.argv[3:]                                   # after "-m stream_service.score.sheet"
say = lambda **k: print(json.dumps(k), flush=True)
if a[0] == "version":
    say(version="t1")
elif a[0] == "make":
    if "broken" in a[1]:
        say(error="ValueError: no notes to write"); sys.exit(1)
    for p in (0.25, 0.5, 1.0):
        say(progress=p); time.sleep(0.3 if "--neural" in a else 0.02)
    pathlib.Path(a[2]).write_text("<mei/>")
    say(done=True, bars=3)
elif a[0] == "pdf":
    pathlib.Path(a[2]).write_bytes(b"%PDF-1.7 " + a[a.index("--paper") + 1].encode())
    say(done=True, bytes=12)
elif a[0] == "pages":
    w = a[a.index("--width") + 1]
    say(pages=[f"<svg>{{w}} 1</svg>", f"<svg>{{w}} 2</svg>"])
'''


class Lib:
    def __init__(self, folder: pathlib.Path) -> None:
        self.folder = folder

    def item(self, piece: str):
        return {"title": "Maple", "composer": "Joplin"} if piece in ("lib:maple", "lib:broken") else None

    def resolve(self, piece: str):
        p = self.folder / f"{piece.split(':')[1]}.mid"
        return p if p.exists() else None


def test_score_routes(tmp_path: pathlib.Path, monkeypatch) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE.format(py=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(score_sheet, "PYTHON", fake)
    monkeypatch.setattr(score_sheet, "MAKE_PYTHON", fake)
    (tmp_path / "maple.mid").write_bytes(b"MThd fake")
    (tmp_path / "broken.mid").write_bytes(b"MThd other")
    sheets = score_sheet.ScoreSheets(Lib(tmp_path), cache=tmp_path / "cache")

    async def go():
        app = web.Application()
        sheets.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            r = await cl.get("/api/v1/score/lib:nope")
            assert r.status == 404 and (await r.json())["error"] == "unknown_piece"
            r = await cl.get("/api/v1/score/lib:maple", params={"w": "400", "h": "700"})
            b = await r.json()                     # the quick pass: in the first answer
            assert r.status == 200 and b["state"] == "ready" and b["title"] == "Maple" and b["kind"] == "quick"
            assert b["pages"] == 2 and b["rev"].endswith("-t1-quick") and b["better"] == "none"
            seen = set()
            for _ in range(100):                   # the better pass, only when asked for
                b = await (await cl.get("/api/v1/score/lib:maple", params={"w": "400", "h": "700",
                                                                             "kind": "better"})).json()
                seen.add(b["state"])
                if b["state"] == "ready":
                    break
                await asyncio.sleep(0.05)
            assert b["state"] == "ready" and b["rev"].endswith("-t1-better") and "making" in seen
            b = await (await cl.get("/api/v1/score/lib:maple", params={"w": "400", "h": "700"})).json()
            assert b["kind"] == "quick" and b["better"] == "ready"
            r = await cl.get("/api/v1/score/lib:maple/page/2.svg", params={"w": "400", "h": "700"})
            assert r.status == 200 and r.content_type == "image/svg+xml" and await r.text() == "<svg>400 2</svg>"
            r = await cl.get("/api/v1/score/lib:maple/page/3.svg", params={"w": "400", "h": "700"})
            assert r.status == 404
            r = await cl.get("/api/v1/score/lib:maple/pdf")
            assert r.status == 200 and r.content_type == "application/pdf" and await r.read() == b"%PDF-1.7 a4"
            assert r.headers["Content-Disposition"].startswith('inline; filename="Maple.pdf"')
            r = await cl.get("/api/v1/score/lib:maple/pdf", params={"paper": "letter", "download": "1"})
            assert await r.read() == b"%PDF-1.7 letter" and r.headers["Content-Disposition"].startswith("attachment")
            assert (await cl.get("/api/v1/score/lib:maple/pdf", params={"paper": "a3"})).status == 400
            b = await (await cl.get("/api/v1/score/lib:broken")).json()
            assert b["state"] == "failed" and "no notes" in b["detail"]
            assert (await (await cl.get("/api/v1/score/lib:broken")).json())["state"] == "failed"   # remembered
            n0 = len(list((tmp_path / "cache").glob("*")))
            b = await (await cl.get("/api/v1/score/lib:broken", params={"retry": "1"})).json()
            assert b["state"] == "failed" and n0 == len(list((tmp_path / "cache").glob("*")))      # tried again
            r = await cl.get("/api/v1/score/lib:broken/page/1.svg")
            assert r.status == 404 and (await r.json())["error"] == "not_ready"
            assert (await cl.get("/api/v1/score/lib:broken/pdf")).status == 404

    asyncio.run(go())
    assert len(list((tmp_path / "cache").glob("*.mei"))) == 2                       # quick + better
    assert not list((tmp_path / "cache").glob("*.pdf")) and not list((tmp_path / "cache").glob(".*.pdf"))   # never kept


def test_abandoned_job_is_dropped(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setattr(score_sheet, "ABANDON_S", -1.0)
    sheets = score_sheet.ScoreSheets(Lib(tmp_path), cache=tmp_path / "cache")

    async def go():
        job = score_sheet._Job("k", "better", tmp_path / "maple.mid", "Maple", "")
        sheets.jobs["k"] = sheets.better["k"] = job
        await sheets._run_better()

    asyncio.run(go())
    assert not sheets.jobs and not sheets.better and not sheets.failed
