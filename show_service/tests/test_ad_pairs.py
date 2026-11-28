"""The review routes (ad_pairs.py): a reel item's verdict is kept and becomes a moment note; a pair's stays blind;
the frames route serves a stretch of the replay's own DMX."""
from __future__ import annotations

import asyncio
import json

import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from show_service import ad_pairs


def _batch(tmp, kind="reel", sides=1):
    d = tmp / "ad_pairs" / "reel-x"
    d.mkdir(parents=True)
    tlp = tmp / "run" / "s1.karaoke.json"
    tlp.parent.mkdir()
    tlp.write_text("{}")
    dmx = np.zeros((200, 512), dtype=np.uint8)
    dmx[:, 0] = np.arange(200) % 256
    np.savez(tlp.with_name("s1.karaoke.dmx.npz"), dmx=dmx, t0_s=0.0, hop_s=0.05)
    item = {"n": 1, "song": "s1", "title": "Song", "t0_s": 2.0, "dur_s": 1.0, "hop_s": 0.05, "sides": sides,
            "kind": "flagged", "at_s": 3.0, "label": "verse → chorus", "flag": "its change came 2.1 s early"}
    (d / "pairs.json").write_text(json.dumps({"batch": "reel-x", "kind": kind, "pairs": [item]}))
    side = {"side": "show", "run": "run", "seed": 0, "timeline": str(tlp), "scores": {}}
    key = {"n": 1, "song": "s1", "what": "section", "check": "lands", "left": side}
    if sides == 2:
        key["right"] = side
    (d / "key.json").write_text(json.dumps({"pairs": [key]}))
    (d / "clip-1.json").write_text(json.dumps({"left": []}))
    return d


def _run(tmp, monkeypatch, fn):
    monkeypatch.setattr(ad_pairs, "PAIRS", tmp / "ad_pairs")
    monkeypatch.setattr(ad_pairs, "FEEDBACK", tmp / "ad_feedback.jsonl")
    app = web.Application()
    def wrap(h):
        async def handler(r):
            return await h(r, None)
        return handler
    for method, path, h in ad_pairs.ROUTES:
        app.router.add_route(method, path, wrap(h))

    async def go():
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return asyncio.run(go())


def test_a_reel_verdict_is_kept_and_noted(tmp_path, monkeypatch):
    d = _batch(tmp_path)

    async def fn(c):
        r = await c.post("/api/v1/eval/pairs/reel-x/1", json={"pick": "wrong", "words": "too early, lands on the pickup"})
        assert r.status == 200
        r = await c.post("/api/v1/eval/pairs/reel-x/1", json={"pick": "bogus"})
        assert r.status == 400
    _run(tmp_path, monkeypatch, fn)
    pick = json.loads((d / "picks.jsonl").read_text().splitlines()[0])
    assert pick["pick"] == "wrong" and pick["words"].startswith("too early")
    note = json.loads((tmp_path / "ad_feedback.jsonl").read_text())
    assert note["scope"] == "moment" and note["t_s"] == 3.0 and note["verdict"] == "bad"
    assert note["dimension"] == "transition" and note["check"] == "lands" and note["from"] == "reel"


def test_skip_and_pair_picks_write_no_note(tmp_path, monkeypatch):
    _batch(tmp_path, kind="draws", sides=2)

    async def fn(c):
        assert (await c.post("/api/v1/eval/pairs/reel-x/1", json={"pick": "left"})).status == 200
        assert (await c.post("/api/v1/eval/pairs/reel-x/1", json={"pick": "skip"})).status == 200
    _run(tmp_path, monkeypatch, fn)
    assert not (tmp_path / "ad_feedback.jsonl").exists()


def test_frames_serve_a_stretch_of_the_replay(tmp_path, monkeypatch):
    _batch(tmp_path, sides=2)

    async def fn(c):
        r = await c.get("/api/v1/eval/pairs/reel-x/1/frames?from=1&to=2")
        assert r.status == 200
        return await r.json()
    out = _run(tmp_path, monkeypatch, fn)
    assert len(out["left"]) == 20 and len(out["right"]) == 20 and out["t0_s"] == 1.0
    import base64
    assert base64.b64decode(out["left"][0])[0] == 20                 # frame 20 = 1.0 s at 0.05 s a frame
