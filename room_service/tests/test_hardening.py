"""Review fixes: the page socket's outbox and limits (LIVE_V1 §2.2a), per-screen control keys
(§3 machine channels), ffmpeg input hardening and the contract defaults."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import pytest

from room_service.config import RoomConfig, Timeouts, config_from_dict
from room_service.pages import CLOSE_BEHIND, CLOSE_RATE, PageClient, PageGone, PageHub
from room_service.snapshot import SnapshotError, extract_clip_frame

from .conftest import Harness
from .fakes import FakePage


class BlockingWS:
    """Stands in for aiohttp's WebSocketResponse: send_str blocks until `gate` opens."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.gate = asyncio.Event()
        self.closed = False
        self.close_code: int | None = None

    async def send_str(self, text: str) -> None:
        await self.gate.wait()
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000, message: bytes = b"") -> bool:
        self.closed, self.close_code = True, code
        return True


async def _settle(ws: BlockingWS, n: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while len(ws.sent) < n:
        assert asyncio.get_running_loop().time() < deadline, ws.sent
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Outbox (LIVE_V1 §2.2a.1)
# ---------------------------------------------------------------------------

async def test_outbox_latest_value_mask_and_policy_controls_in_order():
    ws = BlockingWS()
    c = PageClient("devtest", "kiosk", ws, "127.0.0.1")
    writer = asyncio.get_running_loop().create_task(c.writer(hb_idle_s=10))
    try:
        c.send("freeze", {"fid": "f1"})
        await asyncio.sleep(0.05)                 # the writer is now stuck sending the freeze
        for rev in (1, 2, 3):
            c.send("mask.set", {"rev": rev})
        c.send("unfreeze", {"fid": "f1"})
        c.send("mask.set", {"rev": 4})
        c.send("policy", {"timed_reload": False})
        c.send("live.attach", {"session": "s1"})
        c.send("policy", {"timed_reload": True})
        ws.gate.set()
        await _settle(ws, 5)
        await asyncio.sleep(0.05)
        got = [(m["type"], m["data"]) for m in ws.sent]
        assert got == [("freeze", {"fid": "f1"}), ("unfreeze", {"fid": "f1"}), ("mask.set", {"rev": 4}),
                       ("live.attach", {"session": "s1"}), ("policy", {"timed_reload": True})], got
    finally:
        writer.cancel()


async def test_outbox_over_256kb_unsent_disconnects_with_4008():
    ws = BlockingWS()
    c = PageClient("devtest", "kiosk", ws, "127.0.0.1")
    writer = asyncio.get_running_loop().create_task(c.writer(hb_idle_s=10))
    try:
        blob = "x" * 10_000
        with pytest.raises(PageGone):
            for i in range(100):                  # control messages are never dropped...
                c.send("live.attach", {"session": f"s{i}", "pad": blob})
        assert i < 30                             # ...so the client is cut off at ~256 KB instead
        await asyncio.sleep(0.05)
        assert c.closed and ws.close_code == CLOSE_BEHIND
        with pytest.raises(PageGone):
            c.send("unfreeze", {"fid": "x"})
    finally:
        writer.cancel()


async def test_watchdog_drops_a_client_more_than_5s_behind(tmp_path):
    cfg = config_from_dict({"screens": {"devtest": {}},
                            "service": {"page_keys_file": str(tmp_path / "keys.json")},
                            "timeouts": {"page_dead_s": 10, "page_behind_s": 0.3}})
    hub = PageHub(cfg, on_hello=lambda c: {}, on_message=lambda *a: None, on_closed=lambda c: None)
    ws = BlockingWS()
    c = PageClient("devtest", "kiosk", ws, "127.0.0.1", behind_s=cfg.timeouts.page_behind_s)
    c.hello = {"page_load_id": "p"}
    writer = asyncio.get_running_loop().create_task(c.writer(hb_idle_s=10))
    dog = asyncio.get_running_loop().create_task(hub._watchdog(c))
    try:
        c.send("freeze", {"fid": "f1"})           # stuck in flight: the oldest unsent message ages
        for _ in range(100):
            c.last_recv = time.monotonic()        # the page keeps talking: not "dead", just behind
            if c.closed:
                break
            await asyncio.sleep(0.05)
        assert c.closed and ws.close_code == CLOSE_BEHIND
    finally:
        writer.cancel()
        dog.cancel()


async def test_hb_only_when_idle():
    ws = BlockingWS()
    ws.gate.set()
    c = PageClient("devtest", "kiosk", ws, "127.0.0.1")
    writer = asyncio.get_running_loop().create_task(c.writer(hb_idle_s=0.2))
    try:
        for _ in range(6):                        # busy: something every 0.1 s -> no hb
            c.send("mask.set", {"rev": _})
            await asyncio.sleep(0.1)
        assert all(m["type"] != "hb" for m in ws.sent)
        await asyncio.sleep(0.5)                  # idle -> hb
        assert any(m["type"] == "hb" for m in ws.sent)
    finally:
        writer.cancel()


async def test_inbound_flood_closes_with_a_backoff_code(harness: Harness):
    """> 50 msgs/s: closed with 4029 (not 4008, on which the page reconnects at once)."""
    page = await harness.page()
    for _ in range(80):
        await page.send("hb")
    assert await wait_closed(page) == CLOSE_RATE


def test_contract_defaults():
    t = Timeouts()
    assert t.page_dead_s == 15.0 and t.page_hb_idle_s == 2.0          # §2.2a.5
    assert t.page_behind_s == 5.0 and t.page_behind_bytes == 256 * 1024  # §2.2a.1
    cfg = RoomConfig()
    assert cfg.mask.min_fetch_interval_s == 1.0                       # §2.2b interim: 1/s/screen
    # The page's own 30 s freeze safety must outlast the longest entry handshake.
    assert t.freeze_ack_s + t.snapshot_s + t.create_session_s + t.session_ready_s + t.live_visible_s < 30


# ---------------------------------------------------------------------------
# Per-screen control keys (LIVE_V1 §3 "Machine channels always need keys")
# ---------------------------------------------------------------------------

KEY = "k" * 40


def write_keys(h: Harness, keys: dict) -> None:
    p = h.tmp / "page_keys.json"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(keys, f)


async def wait_closed(page: FakePage, timeout: float = 3.0) -> int | None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not page.ws.closed:
        assert asyncio.get_running_loop().time() < deadline, "socket not closed"
        await asyncio.sleep(0.02)
    return page.ws.close_code


async def test_control_keys_are_ignored(harness: Harness, caplog):
    """Owner ruling, 14 Oct: no keys in the AV stack. A page keys file is never read: kiosks and labs with
    no key, a wrong key or the 'right' one all connect, and nothing reports a key as required."""
    h = harness
    write_keys(h, {"devtest": KEY})
    with caplog.at_level(logging.DEBUG):
        bad = await h.page()                          # kiosk without a key
        assert bad.welcome is not None and not bad.ws.closed
        wrong = FakePage(h.url, role="lab")
        wrong.key = "w" * 40
        await wrong.connect()
        h.pages.append(wrong)
        await wrong.wait_for("welcome")
        viewer = await h.page(role="viewer")
        assert viewer.welcome is not None
        by_url = FakePage(h.url, screen="north-screen")
        by_url.url_key = KEY
        await by_url.connect()
        h.pages.append(by_url)
        await by_url.wait_for("welcome")
        st, s = await h.api("GET", "/api/v1/status")
    assert st == 200
    for sid in ("devtest", "north-screen"):
        assert s["screens"][sid]["policy"]["page_key_required"] is False
    assert KEY not in json.dumps(s) and KEY not in caplog.text


# ---------------------------------------------------------------------------
# ffmpeg input (the clip URL comes from the page's ack)
# ---------------------------------------------------------------------------

async def test_clip_extraction_only_fetches_http(tmp_path):
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"not a video")
    for url in (f"file://{secret}", str(secret), f"concat:{secret}", "subfile:,,start,0,end,0,:/etc/passwd"):
        with pytest.raises(SnapshotError):
            await extract_clip_frame("ffmpeg", url, 0.0, 2.0)


async def test_slow_capture_keeps_the_page_but_a_hung_render_rebuilds_it(tmp_path):
    """A capture over budget (Chrome's 4K PNG encode on a busy CPU) leaves a healthy page:
    rebuilding it would only make the next snapshot colder. A render that never resolves
    wedges the page's serialised render chain, so that page is rebuilt."""
    from room_service.snapshot import PlaywrightSnapshotter

    class Page:
        def __init__(self, hang: bool) -> None:
            self.hang, self._sm_crashed = hang, False

        def is_closed(self) -> bool:
            return False

        async def evaluate(self, js, arg):
            if self.hang:
                await asyncio.sleep(10)

    class Cdp:
        async def send(self, method, params):
            await asyncio.sleep(10)              # slow capture

    cfg = config_from_dict({"snapshot": {"timeout_s": 0.2}})
    snap = PlaywrightSnapshotter(cfg)
    warmed: list[str] = []
    snap._warm = lambda screen: warmed.append(screen)        # don't launch a browser
    st = {"base_url": "/output/devtest.jpg?t=1", "kind": "image", "clip_t": None, "mask": {}, "flags": {}}
    snap._pages["devtest"], snap._cdp["devtest"] = Page(hang=False), Cdp()
    with pytest.raises(SnapshotError, match="capture"):
        await snap.snapshot("devtest", st)
    assert not snap._pages["devtest"]._sm_crashed and warmed == []
    snap._pages["devtest"] = Page(hang=True)
    with pytest.raises(SnapshotError, match="render"):
        await snap.snapshot("devtest", st)
    assert snap._pages["devtest"]._sm_crashed and warmed == ["devtest"]
