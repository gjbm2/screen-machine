"""score_sheet.py: a piece's score is written once on demand (queued, making with progress, ready), its pages laid
out per size, unknown pieces and failures answered; the writer itself (stream_service/score/sheet.py) is faked."""
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
        say(progress=p); time.sleep(0.05)
    pathlib.Path(a[2]).write_text("<mei/>")
    say(done=True, bars=3)
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
            b = await r.json()
            assert r.status == 200 and b["state"] in ("queued", "making") and b["title"] == "Maple"
            for _ in range(100):
                b = await (await cl.get("/api/v1/score/lib:maple", params={"w": "400", "h": "700"})).json()
                if b["state"] == "ready":
                    break
                await asyncio.sleep(0.05)
            assert b["state"] == "ready" and b["pages"] == 2 and b["rev"].endswith("-t1")
            r = await cl.get("/api/v1/score/lib:maple/page/2.svg", params={"w": "400", "h": "700"})
            assert r.status == 200 and r.content_type == "image/svg+xml" and await r.text() == "<svg>400 2</svg>"
            r = await cl.get("/api/v1/score/lib:maple/page/3.svg", params={"w": "400", "h": "700"})
            assert r.status == 404
            for _ in range(100):
                b = await (await cl.get("/api/v1/score/lib:broken")).json()
                if b["state"] == "failed":
                    break
                await asyncio.sleep(0.05)
            assert b["state"] == "failed" and "no notes" in b["detail"]
            r = await cl.get("/api/v1/score/lib:broken/page/1.svg")
            assert r.status == 404 and (await r.json())["error"] == "not_ready"

    asyncio.run(go())
    assert len(list((tmp_path / "cache").glob("*.mei"))) == 1


def test_abandoned_job_is_dropped(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setattr(score_sheet, "ABANDON_S", -1.0)
    sheets = score_sheet.ScoreSheets(Lib(tmp_path), cache=tmp_path / "cache")

    async def go():
        sheets.jobs["k"] = score_sheet._Job("k", tmp_path / "maple.mid", "Maple", "")
        await sheets._run_jobs()

    asyncio.run(go())
    assert not sheets.jobs and not sheets.failed
