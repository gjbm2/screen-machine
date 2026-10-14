"""End-to-end on a real GPU + NVENC: python -m stream_service on :8082, an h264-720p30 session driven through
holding -> go -> live -> exit -> held -> delete, with the media socket captured and decoded by ffmpeg.

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] [SM_TEST_OUT=/path/for/pngs] pytest stream_service/tests -k integration
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import time

import aiohttp
import numpy as np
import pytest

pytestmark = pytest.mark.gpu

from .conftest import BASE, GPU, PORT
FPS = 30
ANCHOR = (200, 60, 30)
TARGET = (30, 90, 200)


class Events:
    def __init__(self, ws):
        self.ws = ws
        self.items: list[dict] = []
        self.cond = asyncio.Condition()
        self.task = asyncio.create_task(self._read())

    async def _read(self):
        async for m in self.ws:
            ev = json.loads(m.data)
            async with self.cond:
                self.items.append(ev)
                self.cond.notify_all()

    async def wait(self, pred, timeout=10.0) -> dict:
        async def _w():
            async with self.cond:
                while True:
                    for ev in self.items:
                        if pred(ev):
                            return ev
                    await self.cond.wait()
        return await asyncio.wait_for(_w(), timeout)


class Media:
    def __init__(self, ws):
        self.ws = ws
        self.config = None
        self.init = None
        self.frames: list[tuple[int, bool, bytes]] = []
        self.eos = False
        self.task = asyncio.create_task(self._read())

    async def _read(self):
        async for m in self.ws:
            if m.type == aiohttp.WSMsgType.TEXT:
                d = json.loads(m.data)
                if d["type"] == "config":
                    self.config = d
                elif d["type"] == "eos":
                    self.eos = True
            elif m.type == aiohttp.WSMsgType.BINARY:
                b = m.data
                if b[0] == 0x01:
                    self.init = b[1:]
                elif b[0] == 0x02:
                    self.frames.append((int.from_bytes(b[2:6], "big"), b[1] == 1, b[6:]))


def decode_frame(mp4: pathlib.Path, index: int, out: pathlib.Path) -> np.ndarray:
    from PIL import Image

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp4), "-vf", f"select=eq(n\\,{index})",
                    "-frames:v", "1", str(out)], check=True)
    return np.asarray(Image.open(out).convert("RGB")).astype(np.int16)


def close_to(img: np.ndarray, rgb, tol=5) -> bool:
    """Flat (per-channel std < 3) and within tol of rgb."""
    px = img.reshape(-1, 3)
    return bool(np.all(np.abs(px.mean(axis=0) - np.array(rgb)) <= tol) and px.std(axis=0).max() < 3)


async def test_integration_session_lifecycle(service, tmp_path):
    out_dir = pathlib.Path(os.environ.get("SM_TEST_OUT", tmp_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as http:
        ev = Events(await http.ws_connect(f"{BASE}/api/events"))
        body = {"screen": "devtest", "gpu": GPU, "profile": "h264-720p30", "anchor": {"rgb": list(ANCHOR)},
                "scene": "fractal-zoom",
                "transitions": {"in": {"transition": "crossfade", "params": {"duration_s": 2.0, "easing": "linear"}},
                                "out": {"preset": "crossfade-1s", "params": {"easing": "linear"}}},
                "ttl_s": 60, "idempotency_key": f"it-{time.time()}"}
        t0 = time.monotonic()
        r = await http.post(f"{BASE}/api/sessions", json=body)
        assert r.status == 201, await r.text()
        res = await r.json()
        sid = res["session"]
        assert res["state"] == "starting"
        att = res["attach"]
        assert att["url"].startswith(f"ws://127.0.0.1:{PORT}/media/{sid}?k=")
        assert (att["codec"], att["w"], att["h"], att["fps"], att["target_buffer_ms"]) == ("h264", 1280, 720, 30, 300)
        # the same idempotency key returns the same session
        r2 = await http.post(f"{BASE}/api/sessions", json=body)
        assert (await r2.json())["session"] == sid
        media = Media(await http.ws_connect(att["url"]))

        await ev.wait(lambda e: e["type"] == "session.ready" and e["session"] == sid, 5.0)
        t_ready = time.monotonic() - t0
        assert (await (await http.get(f"{BASE}/api/sessions/{sid}")).json())["state"] == "holding"
        await asyncio.sleep(1.5)

        r = await http.post(f"{BASE}/api/sessions/{sid}/go", json={})
        assert r.status == 200 and (await r.json())["state"] == "entering"
        assert (await http.post(f"{BASE}/api/sessions/{sid}/go", json={})).status == 409
        in_start = await ev.wait(lambda e: e["type"] == "session.transition" and e["kind"] == "in"
                                 and e["phase"] == "started")
        in_done = await ev.wait(lambda e: e["type"] == "session.transition" and e["kind"] == "in"
                                and e["phase"] == "done", 5.0)
        assert in_done["pts"] - in_start["pts"] == pytest.approx(2.0, abs=1e-6)
        await ev.wait(lambda e: e["type"] == "session.state" and e.get("state") == "live")

        r = await http.patch(f"{BASE}/api/sessions/{sid}", json={"params": {"zoom_rate": 0.6}})
        assert r.status == 200 and (await r.json())["scene_params"]["zoom_rate"] == 0.6
        r = await http.patch(f"{BASE}/api/sessions/{sid}", json={"params": {"speed": 1}})
        assert r.status == 400 and (await r.json())["error"] == "unknown_param"
        await asyncio.sleep(2.0)

        r = await http.post(f"{BASE}/api/sessions/{sid}/exit", json={"target": {"rgb": list(TARGET)}})
        assert r.status == 202 and (await r.json())["state"] == "exiting"
        out_start = await ev.wait(lambda e: e["type"] == "session.transition" and e["kind"] == "out"
                                  and e["phase"] == "started")
        out_done = await ev.wait(lambda e: e["type"] == "session.transition" and e["kind"] == "out"
                                 and e["phase"] == "done", 5.0)
        held = await ev.wait(lambda e: e["type"] == "session.held", 5.0)
        assert held["hold_pts"] == out_done["pts"]
        assert out_done["pts"] - out_start["pts"] == pytest.approx(1.0, abs=1e-6)
        summary = await (await http.get(f"{BASE}/api/sessions/{sid}")).json()
        assert summary["state"] == "held" and summary["hold_pts"] == held["hold_pts"]
        assert summary["clients"] == 1
        await asyncio.sleep(1.0)
        summary = await (await http.get(f"{BASE}/api/sessions/{sid}")).json()

        r = await http.delete(f"{BASE}/api/sessions/{sid}")
        assert r.status == 204
        ended = await ev.wait(lambda e: e["type"] == "session.ended", 5.0)
        assert ended["reason"] == "deleted"
        await asyncio.wait_for(media.task, 5.0)
        assert media.eos
        await ev.ws.close()

    # -- the stream --------------------------------------------------------------
    cfg = media.config
    assert cfg["codec"] == "h264" and cfg["codec_string"].startswith("avc1.") and cfg["fps"] == FPS
    assert cfg["timescale"] == 90000 and cfg["frame_duration"] == 3000
    fids = [f for f, _, _ in media.frames]
    assert fids[0] == 0 and media.frames[0][1], "joined before ready: the stream starts at the first IDR"
    assert fids == list(range(fids[0], fids[0] + len(fids))), "frames are contiguous (no drops)"
    assert [f for f, k, _ in media.frames if k] == list(range(0, fids[-1] + 1, FPS)), "an IDR every second"
    mp4 = out_dir / "capture.mp4"
    mp4.write_bytes(media.init + b"".join(p for _, _, p in media.frames))
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of",
                                       "json", str(mp4)], capture_output=True, text=True, check=True).stdout)
    st = probe["streams"][0]
    assert (st["color_space"], st["color_transfer"], st["color_primaries"], st["color_range"]) == \
        ("bt709", "iec61966-2-1", "bt709", "tv")

    f = lambda pts: round(pts * FPS)  # noqa: E731
    s_in, d_in, s_out, hold = f(in_start["pts"]), f(in_done["pts"]), f(out_start["pts"]), f(held["hold_pts"])
    shots = {"1_anchor_hold": s_in - 5, "2_mid_crossfade_in": s_in + 30, "3_live_zoom": d_in + 30,
             "4_mid_crossfade_out": s_out + 15, "5_before_hold": hold - 1, "6_hold_pts": hold,
             "7_held_target": hold + 20}
    img = {k: decode_frame(mp4, n, out_dir / f"{k}.png") for k, n in shots.items()}
    assert close_to(img["1_anchor_hold"], ANCHOR)
    assert close_to(img["6_hold_pts"], TARGET), "the hold_pts frame shows the exit target fully"
    assert close_to(img["7_held_target"], TARGET)
    assert not close_to(img["5_before_hold"], TARGET, tol=2), "the frame before hold_pts is still mid-transition"
    assert img["3_live_zoom"].std() > 15, "the live scene is a fractal, not a flat colour"
    for k in ("2_mid_crossfade_in", "4_mid_crossfade_out"):
        assert not close_to(img[k], ANCHOR, 10) and not close_to(img[k], TARGET, 10)
    # a linear crossfade at 0.5 is the average of its ends (+-codec error): check against the anchor side
    mid = img["2_mid_crossfade_in"].reshape(-1, 3).mean(axis=0)
    assert np.all(np.abs(mid - (np.array(ANCHOR) + img["3_live_zoom"].reshape(-1, 3).mean(axis=0)) / 2) < 40)

    late = summary["late_frames"]
    print(f"\nready after {t_ready:.2f} s; frames {summary['frames']}, late {late}, render_ms {summary['render_ms']}, "
          f"write_ms {summary['stats']['write_ms']}, encode_ms {summary['stats']['encode_ms']}; pngs in {out_dir}")
    assert t_ready < 5.0
    assert late <= max(3, 0.02 * summary["frames"])


async def test_integration_letterbox_scene_changes_and_reexit(service, tmp_path):
    """PNG anchor of another aspect (letterboxed), cut in, two in-stream scene changes, a re-exit while
    exiting (the first out transition is superseded), and an exit from held (additive)."""
    import base64
    import io

    from PIL import Image

    out_dir = pathlib.Path(os.environ.get("SM_TEST_OUT", tmp_path))
    png = io.BytesIO()
    Image.new("RGB", (640, 480), (220, 20, 20)).save(png, "PNG")     # 4:3 -> pillarboxed in 16:9
    async with aiohttp.ClientSession() as http:
        ev = Events(await http.ws_connect(f"{BASE}/api/events"))
        body = {"screen": "devtest", "gpu": GPU, "profile": "h264-720p30",
                "anchor": {"png_b64": base64.b64encode(png.getvalue()).decode()},
                "scene": {"source": "fractal_zoom", "params": {"preset": "dendrite", "palette": "neon"}},
                "transitions": {"in": "cut", "out": "crossfade-1s"}, "ttl_s": 60}
        res = await (await http.post(f"{BASE}/api/sessions", json=body)).json()
        sid = res["session"]
        media = Media(await http.ws_connect(res["attach"]["url"]))
        mine = lambda pred: (lambda e: e.get("session") == sid and pred(e))  # noqa: E731
        await ev.wait(mine(lambda e: e["type"] == "session.ready"), 5.0)
        await asyncio.sleep(0.5)
        assert (await http.post(f"{BASE}/api/sessions/{sid}/go", json={})).status == 200
        in_done = await ev.wait(mine(lambda e: e["type"] == "session.transition" and e["kind"] == "in"
                                     and e["phase"] == "done"))
        await ev.wait(mine(lambda e: e.get("state") == "live"))
        await asyncio.sleep(1.0)
        r = await http.patch(f"{BASE}/api/sessions/{sid}",
                             json={"scene": "black", "transition": {"transition": "crossfade",
                                                                    "params": {"duration_s": 0.5}}})
        assert r.status == 200 and (await r.json())["scene"] == "black"
        sc1 = await ev.wait(mine(lambda e: e["type"] == "session.transition" and e["kind"] == "scene"
                                 and e["phase"] == "done"))
        r = await http.patch(f"{BASE}/api/sessions/{sid}",
                             json={"scene": {"source": "solid", "params": {"rgb": "#00ff00"}},
                                   "transition": "cut"})
        assert r.status == 200
        await ev.wait(mine(lambda e: e["type"] == "session.transition" and e["kind"] == "scene"
                           and e["phase"] == "done" and e["pts"] > sc1["pts"]))
        await asyncio.sleep(0.5)
        r = await http.post(f"{BASE}/api/sessions/{sid}/exit", json={"target": {"rgb": [0, 0, 255]}})
        assert r.status == 202
        await ev.wait(mine(lambda e: e["type"] == "session.transition" and e["kind"] == "out"
                           and e["phase"] == "started"))
        await asyncio.sleep(0.3)       # mid-way through the 1 s crossfade: re-exit to another target with a cut
        r = await http.post(f"{BASE}/api/sessions/{sid}/exit",
                            json={"target": {"rgb": [255, 255, 0]}, "transition": "cut"})
        assert r.status == 202
        held1 = await ev.wait(mine(lambda e: e["type"] == "session.held"), 5.0)
        sup = await ev.wait(mine(lambda e: e["type"] == "session.transition" and e["kind"] == "out"
                                 and e.get("superseded")))
        assert sup["pts"] == held1["hold_pts"]     # the first out was discarded by the cut's completion
        assert sum(1 for e in ev.items if e.get("session") == sid and e["type"] == "session.held") == 1
        await asyncio.sleep(0.5)
        # exit again from held (additive): a new target and a new hold_pts
        r = await http.post(f"{BASE}/api/sessions/{sid}/exit",
                            json={"target": {"rgb": [255, 0, 255]},
                                  "transition": {"transition": "crossfade", "params": {"duration_s": 0.5}}})
        assert r.status == 202
        held2 = await ev.wait(mine(lambda e: e["type"] == "session.held" and e["hold_pts"] > held1["hold_pts"]), 5)
        assert held2["hold_pts"] - held1["hold_pts"] > 0.5
        await asyncio.sleep(0.5)
        assert (await http.delete(f"{BASE}/api/sessions/{sid}")).status == 204
        await asyncio.wait_for(media.task, 5.0)
        await ev.ws.close()

    mp4 = out_dir / "capture2.mp4"
    mp4.write_bytes(media.init + b"".join(p for _, _, p in media.frames))
    first = decode_frame(mp4, 0, out_dir / "lb_anchor.png")
    assert close_to(first[:, 170:1110], (220, 20, 20), 6), "the 4:3 anchor fills the middle 960 px"
    assert close_to(first[:, :150], (0, 0, 0), 4) and close_to(first[:, 1130:], (0, 0, 0), 4), "black side bars"
    fid = lambda pts: round(pts * FPS)  # noqa: E731
    live = decode_frame(mp4, fid(in_done["pts"]), out_dir / "cut_in_first_live.png")
    assert live.std() > 15, "cut: the scene is fully on screen on the in transition's first frame"
    assert close_to(decode_frame(mp4, fid(held1["hold_pts"]) + 3, out_dir / "held1.png"), (255, 255, 0), 6)
    assert close_to(decode_frame(mp4, fid(held2["hold_pts"]) + 5, out_dir / "held2.png"), (255, 0, 255), 6)
