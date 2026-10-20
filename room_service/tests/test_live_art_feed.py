"""live_art_feed.py + the art route: the ClipCycle port, the TV's art pick, the feed's change-only work (skipped
during a cut), the light mask (the TV's, quantised; a mask-only change redone only for the show's value) and the
GET /api/v1/screens/{id}/art contract. Fakes for Chrome and ffmpeg; a fake Flask over real HTTP for the HEADs."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from email.utils import formatdate
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from room_service import live_art, live_art_feed as laf
from room_service.config import RoomConfig
from room_service.mask import MaskPusher, MaskValue

D = 10.0
CRUISE = D / 0.6 - (1.5 * 0.065 + 2.5 * (0.6 + 0.065) / 2) - 2.5      # ClipCycle's playUntil, by hand: 13.2379
T_HOLD = 1.5 + 2.5 + CRUISE + 2.5                                       # 19.7379
T_XFADE = T_HOLD + 4.0
CYCLE = T_XFADE + 8.0 + 2.0                                              # 33.7379


# -- the ClipCycle port -----------------------------------------------------------------------------------------

def test_timeline_matches_clipcycle_by_hand():
    tl = laf.ClipTimeline(D)
    assert tl.cruise_s == pytest.approx(13.2379167, abs=1e-6)
    assert (tl.t_hold, tl.t_crossfade, tl.cycle) == pytest.approx((T_HOLD, T_XFADE, CYCLE))
    hand = {
        0.0: 0.0,
        1.0: 0.065,                                                   # primer: 0.065x
        1.5: 0.0975,                                                  # end of primer
        2.75: 0.0975 + 0.065 * 1.25 + 0.535 * 2.5 * (2 * 0.5 ** 3 / 3),   # mid ease-in: 0.290208
        4.0: 0.0975 + 2.5 * 0.3325,                                   # ease-in done: 0.92875
        10.0: 0.92875 + 0.6 * 6,                                      # cruise at 0.6x: 4.52875
        4.0 + CRUISE + 1.25: 0.92875 + 0.6 * CRUISE + 0.75 - 0.535 * 2.5 / 12,   # mid ease-out: 9.510042
        T_HOLD: D - 0.29725,                                          # where the clip stops: 9.70275
        T_HOLD + 3.9: D - 0.29725,                                    # held on that frame
        T_XFADE + 5: D - 0.29725,                                     # the video stays there under the fade
    }
    for t, m in hand.items():
        assert tl.media_time(t) == pytest.approx(m, abs=1e-6), t
    assert tl.media_time(CYCLE + 1.0) == pytest.approx(0.065)        # wraps: the next cycle
    assert tl.first_frame_weight(T_XFADE - 0.01) == 0.0
    assert tl.first_frame_weight(T_XFADE + 4.0) == pytest.approx(0.5, abs=1e-6)          # symmetric easing
    assert tl.first_frame_weight(T_XFADE + 8 * 0.3390625) == pytest.approx(0.15625, abs=1e-6)   # bezier s=0.25
    assert tl.first_frame_weight(T_XFADE + 8.5) == 1.0               # restart delay: the first frame
    assert tl.phase_of(0.0) == 0.0
    assert tl.phase_of(0.065) == pytest.approx(1.0, abs=1e-6)
    assert tl.phase_of(4.52875) == pytest.approx(10.0, abs=1e-6)
    assert tl.phase_of(99.0) == pytest.approx(T_HOLD, abs=1e-6)       # past the stop: the hold's start


def test_short_clip_has_no_cruise_and_stops_at_its_end():
    tl = laf.ClipTimeline(1.0)
    assert tl.cruise_s == 0.0 and tl.cycle == pytest.approx(20.5)
    assert tl.media_time(6.4) == 1.0 and tl.media_time(10) == 1.0


def test_plan_samples_one_frame_per_second():
    tl = laf.ClipTimeline(D)
    step, s = laf.plan_video(tl, 24.0, 240)
    assert step == 1.0 and len(s) == 34                               # ceil(33.74)
    assert s[0] == (0, 0.0) and s[1] == (1, 0.0) and s[10] == (108, 0.0)    # floor(4.52875 * 24)
    assert s[20] == (232, 0.0) and s[23] == (232, 0.0)                # holdFinal: floor(9.70275 * 24)
    assert s[24][0] == 232 and 0 < s[24][1] < 0.01                    # the fade starts slowly (ease)
    assert s[28][0] == 232 and s[28][1] == pytest.approx(tl.first_frame_weight(28.0), abs=1e-4)
    assert s[32] == (0, 0.0) and s[33] == (0, 0.0)                    # restart delay: the first frame itself
    step, s = laf.plan_video(laf.ClipTimeline(300.0), 24.0, 7200)
    assert step == 3.0 and len(s) <= laf.MAX_SAMPLES                  # a long clip: sampled every 3 s


# -- the TV's art pick ------------------------------------------------------------------------------------------

def h(lm: float | None, etag: str | None = None, length: str = "10") -> tuple[int, dict]:
    if lm is None:
        return 404, {}
    return 200, {"lm": formatdate(lm, usegmt=True), "etag": etag, "len": length}


def test_choose_art_like_use_file_polling():
    pick = lambda **kw: laf.choose_art("s", {"jpg": kw.get("jpg", h(None)), "JPG": kw.get("JPG", h(None)),
                                             "mp4": kw.get("mp4", h(None))})
    assert pick() is None
    assert pick(jpg=h(100), mp4=h(50)).ext == "jpg"
    assert pick(jpg=h(100), mp4=h(100)).ext == "mp4"                  # ties: mp4 first
    assert pick(jpg=h(100), JPG=h(100)).ext == "JPG"                  # then JPG
    a = pick(jpg=h(100, '"e1"'))
    assert a.base_url == "/output/s.jpg?t=100000" and a.kind == "image" and a.ident == 'jpg|"e1"'
    assert pick(jpg=h(100, '"e2"')).ident != a.ident                  # same second, new file: new identity
    assert pick(jpg=h(100, None, "10")).ident != pick(jpg=h(100, None, "11")).ident   # no ETag: LM + length
    assert laf.choose_art("s", {"jpg": None, "JPG": h(None), "mp4": h(None)}) is laf.UNKNOWN   # a HEAD failed


# -- the feed ---------------------------------------------------------------------------------------------------

class FakeFlask:
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.heads = 0

    async def head(self, request: web.Request) -> web.Response:
        self.heads += 1
        f = self.files.get(request.match_info["name"])
        if f is None:
            return web.Response(status=404)
        return web.Response(status=200, headers={"Last-Modified": formatdate(f["lm"], usegmt=True),
                                                 "ETag": f["etag"]})


class FakeCapturer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, tuple]] = []
        self.delay = 0.0
        self.fail = False

    async def capture(self, sid, state, vp):
        self.calls.append((sid, state, vp))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("page crashed")
        return b"\xff\xd8 still %d" % len(self.calls)

    async def close(self):
        pass


class FakeDecoder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.blends: list[tuple] = []
        self.on_decode = None                    # a hook run as the decode pass ends (a cut starting, say)
        self.delay = 0.0
        self.cancelled = 0

    async def probe(self, url):
        return {"duration": D, "fps": 24.0, "nb_frames": 240, "in_matrix": "auto"}

    async def decode(self, url, indices, vp, out_dir, timeout_s, in_matrix="auto", mask=None):
        self.calls.append((url, list(indices), vp, in_matrix, mask))
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        out = []
        for i, idx in enumerate(indices):
            p = out_dir / f"s{i + 1:05d}.jpg"
            Image.new("RGB", (16, 9), (idx % 256, 0, 0)).save(p, "JPEG")
            out.append(p)
        if self.on_decode:
            self.on_decode()
        return out

    async def blend(self, a, b, weights, outs, timeout_s):
        self.blends.append((a.name, b.name, list(weights)))
        with Image.open(a) as ia, Image.open(b) as ib:
            for w, o in zip(weights, outs):
                Image.blend(ia.convert("RGB"), ib.convert("RGB"), w).save(o, "JPEG")


class FakeCtl:
    def __init__(self, sid: str, live: bool = True) -> None:
        self.id = sid
        self.cfg = SimpleNamespace(live_enabled=live, profile="hevc-2160p60")
        self.phase, self._handshake, self._shot, self.session = "idle", None, None, None
        self.layer, self.desired, self.kiosk_state = "art", "art", None

    def _render_size(self, profile):
        return {"width": 640, "height": 360}


class FakeScreens:
    def __init__(self, *ctls: FakeCtl) -> None:
        self.controllers = {c.id: c for c in ctls}

    def get(self, sid):
        return self.controllers.get(sid)


class FakeMask:
    """svc.mask as MaskPusher / MaskArbiter: `pushed` (what the pages show), `latest` (last fetched from Flask),
    status(sid) = the pushed value else the last fetched, the room-wide sensor_mask suspension and the screen's
    sensor hold."""

    def __init__(self) -> None:
        self.pushed: dict[str, dict] = {}
        self.latest: dict[str, dict] = {}
        self.suspended = False
        self.held: set[str] = set()

    def status(self, sid):
        v = self.pushed.get(sid) or self.latest.get(sid) or {"brightness": None, "warm_hex": None, "warm_alpha": None}
        return {**v, "suspended": self.suspended, "held": sid in self.held}


DIM = {"brightness": 0.4, "warm_hex": "#FFC871", "warm_alpha": 0.1}


@pytest.fixture
async def rig(tmp_path):
    flask = FakeFlask()
    fapp = web.Application()
    fapp.router.add_route("HEAD", "/output/{name}", flask.head)
    fsrv = TestServer(fapp, host="127.0.0.1")
    await fsrv.start_server()
    cfg = RoomConfig()
    cfg.flask_url = str(fsrv.make_url("")).rstrip("/")
    cfg.art_page_base = "http://127.0.0.1:5000"
    cfg.state_dir = tmp_path
    http = ClientSession()
    svc = SimpleNamespace(cfg=cfg, http=http, screens=FakeScreens(FakeCtl("devtest"), FakeCtl("north-screen"),
                                                                   FakeCtl("plain", live=False)), mask=FakeMask())
    cap, dec = FakeCapturer(), FakeDecoder()
    feed = laf.LiveArtFeed(svc, capturer=cap, decoder=dec, tick_s=0.02)
    yield SimpleNamespace(flask=flask, svc=svc, feed=feed, cap=cap, dec=dec, cfg=cfg, mask=svc.mask)
    await feed.close()
    await http.close()
    await fsrv.close()


async def until(pred, timeout=3.0, what="condition"):
    t0 = time.monotonic()
    while not pred():
        if time.monotonic() - t0 > timeout:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


async def test_still_captured_once_per_publish_with_the_tvs_mask(rig):
    rig.mask.pushed["devtest"] = {"brightness": 0.5512, "warm_hex": "#ffc871", "warm_alpha": 0.1}
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    await rig.feed.start()
    await until(lambda: len(rig.cap.calls) == 1, what="first capture")
    await asyncio.sleep(0.2)                                           # ~10 ticks, nothing changed
    assert len(rig.cap.calls) == 1
    sid, state, vp = rig.cap.calls[0]
    assert sid == "devtest" and vp == (640, 360)
    assert state["mask"] == {"brightness": 0.55, "warm_hex": "#FFC871", "warm_alpha": 0.1}   # quantised to 0.005
    assert state["flags"] == {"nomask": False, "nocolour": False, "fit": "fit"} and state["kind"] == "image"
    assert rig.feed.status()["devtest"]["mask"] == "m110FFC871020"
    assert state["base_url"].startswith("/output/devtest.jpg?t=1000000&v=")
    first = rig.feed.lookup("devtest")
    assert first.status == "ok" and first.kind == "still" and first.body.startswith(b"\xff\xd8")
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"b"'}       # republished within the same second
    await until(lambda: len(rig.cap.calls) == 2, what="recapture")
    assert rig.cap.calls[1][1]["base_url"] != state["base_url"]       # a new URL: Chrome can't reuse the old
    await until(lambda: rig.feed.lookup("devtest").etag != first.etag, what="new frame")
    assert rig.feed.lookup("plain").status == "off"                   # not live-enabled: no feed
    assert rig.feed.lookup("north-screen").status == "no_art"         # no /output file at all


# -- the light mask ---------------------------------------------------------------------------------------------

def test_mask_key_quantises_and_mask_factors_follow_the_page():
    k = laf.mask_key({"brightness": 0.5512, "warm_hex": "#ffb070", "warm_alpha": 0.3011})
    assert k == (110, "#FFB070", 60)
    assert laf.mask_of(k) == {"brightness": 0.55, "warm_hex": "#FFB070", "warm_alpha": 0.3}
    assert laf.mask_key({"brightness": 0.5524, "warm_hex": "#FFB070", "warm_alpha": 0.3024}) == k   # noise: same
    assert laf.mask_key({"brightness": 0.5526, "warm_hex": "#FFB070", "warm_alpha": 0.3}) == (111, "#FFB070", 60)
    assert laf.mask_key({"brightness": 0.55, "warm_hex": "#FFB071", "warm_alpha": 0.3})[1] == "#FFB071"   # exact
    assert laf.mask_key(None) == laf.mask_key({"brightness": None}) == (200, "#FFFFFF", 0)   # unknown: neutral
    assert laf.mask_key({"brightness": -1, "warm_hex": "red", "warm_alpha": 1.7}) == (0, "#FFFFFF", 200)   # clamps
    assert laf.mask_tag(k) == "m110FFB070060"
    # The page: black at opacity 1 - b, then warm_hex multiplied in at opacity a: in * b * (1 - a + a * W).
    f = laf.mask_factors({"brightness": 0.55, "warm_hex": "#FFB070", "warm_alpha": 0.3})
    assert f == pytest.approx((0.55, 0.55 * (0.7 + 0.3 * 0xB0 / 255), 0.55 * (0.7 + 0.3 * 0x70 / 255)))
    assert f == pytest.approx((0.55, 0.498882, 0.457471), abs=1e-6)
    assert laf.mask_filter(laf.NEUTRAL_MASK) == "" and laf.mask_filter(None) == ""
    assert laf.mask_filter({"brightness": 1.0, "warm_hex": "#FFFFFF", "warm_alpha": 0.8}) == ""   # white: nothing


async def test_mask_sources_are_ranked_and_a_held_value_is_latched(rig):
    """A room restart mid-show: the suspension is restored, nothing is ever pushed while it lasts, and the page
    follows Flask's /mask by itself (it polls until a value is pushed on its connection), so both fallbacks move."""
    m = MaskPusher(rig.cfg, None, None, is_frozen=lambda s: False)    # the real one: status() = pushed else latest
    rig.svc.mask = m
    st = laf._Screen("devtest", rig.svc.screens.controllers["devtest"])
    assert rig.feed._mask_now(st) == (None, False)                     # nothing known yet
    m.latest["devtest"] = MaskValue(0.62, "#FFD6A0", 0.05)
    assert rig.feed._mask_now(st) == ((124, "#FFD6A0", 10), False)     # Flask's last fetched value
    st.ctl.kiosk_state = {"mask": {"brightness": 0.2, "warm_hex": "#FFC871", "warm_alpha": 0.1}}
    assert rig.feed._mask_now(st) == ((40, "#FFC871", 20), False)      # what the page draws beats the fetch
    m.set_suspended(True)
    assert m.status("devtest")["brightness"] == 0.62 and m.pushed == {}
    assert rig.feed._mask_now(st) == ((40, "#FFC871", 20), True)
    st.ctl.kiosk_state = {"mask": {"brightness": 0.3, "warm_hex": "#FFD0A0", "warm_alpha": 0.1}}
    m.latest["devtest"] = MaskValue(0.5, "#FFD6A0", 0.05)
    assert rig.feed._mask_now(st) == ((40, "#FFC871", 20), True)       # latched: the moving values are ignored
    m.pushed["devtest"] = MaskValue(**DIM)                             # a push is a better source: taken once
    assert rig.feed._mask_now(st) == ((80, "#FFC871", 20), True)
    m.pushed["devtest"] = MaskValue(**{**DIM, "brightness": 0.3})
    assert rig.feed._mask_now(st) == ((80, "#FFC871", 20), True)
    m.set_suspended(False)                                             # the hold ends: the push again
    assert rig.feed._mask_now(st) == ((60, "#FFC871", 20), False) and st.latch is None
    # Latched in an empty start (nothing known: None), then the first value seen.
    st.ctl.kiosk_state, m.latest, m.pushed = None, {}, {}
    m.set_suspended(True)
    assert rig.feed._mask_now(st) == (None, True)
    m.latest["devtest"] = MaskValue(0.62, "#FFD6A0", 0.05)
    assert rig.feed._mask_now(st) == ((124, "#FFD6A0", 10), True)
    m.latest["devtest"] = MaskValue(0.7, "#FFD6A0", 0.05)
    assert rig.feed._mask_now(st)[0] == (124, "#FFD6A0", 10)
    # A sensor hold's latch is retaken when the show's suspension starts.
    m.set_suspended(False)
    assert rig.feed._mask_now(st)[1] is False and st.latch is None
    fake = rig.svc.mask = FakeMask()
    fake.pushed["devtest"], fake.held = dict(DIM), {"devtest"}
    assert rig.feed._mask_now(st) == ((80, "#FFC871", 20), True)
    fake.pushed["devtest"] = {**DIM, "brightness": 0.3}                # (the Flask tier pushes under a sensor hold)
    assert rig.feed._mask_now(st)[0] == (80, "#FFC871", 20)
    fake.suspended = True
    assert rig.feed._mask_now(st)[0] == (60, "#FFC871", 20)


async def test_restart_mid_show_moving_fallback_never_starves_the_decode(rig):
    m = MaskPusher(rig.cfg, None, None, is_frozen=lambda s: False)
    m.set_suspended(True)                                              # restored from sensor_mask.json
    rig.svc.mask = m
    ctl = rig.svc.screens.controllers["devtest"]
    ctl.layer = "live"
    ctl.kiosk_state = {"mask": {"brightness": 0.4, "warm_hex": "#FFC871", "warm_alpha": 0.1}}
    m.latest["devtest"] = MaskValue(0.62, "#FFD6A0", 0.05)
    rig.dec.delay = 0.3
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:                                 # both fallbacks drift every 50 ms
        b = 0.4 + 0.01 * ((time.monotonic() - t0) // 0.05)
        ctl.kiosk_state = {"mask": {"brightness": b, "warm_hex": "#FFC871", "warm_alpha": 0.1}}
        m.latest["devtest"] = MaskValue(b + 0.2, "#FFD6A0", 0.05)
        await asyncio.sleep(0.05)
    assert isinstance(st.committed, laf.VideoFrames) and st.have[2] == (80, "#FFC871", 20)   # the first value
    assert len(rig.dec.calls) == 1 and rig.dec.cancelled == 0
    assert rig.feed.lookup("devtest").status == "ok"


async def test_mask_only_change_waits_in_normal_and_is_redone_when_held(rig):
    m, calls = rig.mask, rig.cap.calls
    m.pushed["devtest"] = dict(DIM)
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    await rig.feed.start()
    await until(lambda: rig.feed.status()["devtest"]["mask"] == "m080FFC871020", what="first capture")
    first = rig.feed.lookup("devtest").etag
    m.pushed["devtest"] = {**DIM, "brightness": 0.3}                   # Normal: the sensor moves the mask
    await asyncio.sleep(0.2)                                           # ~10 ticks
    assert len(calls) == 1 and rig.feed.lookup("devtest").etag == first
    rig.flask.files["devtest.jpg"] = {"lm": 1001, "etag": '"b"'}       # new art: with the mask current then
    await until(lambda: len(calls) == 2, what="new art captured")
    assert calls[1][1]["mask"]["brightness"] == 0.3
    m.pushed["devtest"] = {**DIM, "brightness": 0.25}
    m.suspended = True                                                 # a live mode's first step holds the mask
    await until(lambda: len(calls) == 3, what="recapture with the held value")
    assert calls[2][1]["mask"]["brightness"] == 0.25 and calls[2][1]["base_url"] == calls[1][1]["base_url"]
    await until(lambda: rig.feed.lookup("devtest").etag.startswith("sm050FFC871020-"), what="held frame served")
    m.pushed["devtest"] = {**DIM, "brightness": 0.1}                   # held: fixed for the show
    await asyncio.sleep(0.2)
    assert len(calls) == 3
    m.suspended = False                                                # Normal again: the 0.1 waits for new art
    m.pushed["devtest"] = {**DIM, "brightness": 0.2}
    await asyncio.sleep(0.1)
    assert len(calls) == 3
    m.held.add("devtest")                                              # a sensor hold (calibration) counts too
    await until(lambda: len(calls) == 4, what="recapture for a sensor hold")
    assert calls[3][1]["mask"]["brightness"] == 0.2


async def test_show_bound_screen_redoes_a_following_mask_now_and_then(rig, monkeypatch):
    monkeypatch.setattr(laf, "MASK_FOLLOW_REDO_S", 0.4)
    m, calls, ctl = rig.mask, rig.cap.calls, rig.svc.screens.controllers["devtest"]
    m.pushed["devtest"] = dict(DIM)
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: st.have is not None, what="first capture")
    ctl.desired = {"live": {"scene": "live-art"}}                      # heading live, the sensor still following
    m.pushed["devtest"] = {**DIM, "brightness": 0.3}
    await asyncio.sleep(0.15)
    assert len(calls) == 1                                             # at most once per MASK_FOLLOW_REDO_S
    await until(lambda: len(calls) == 2, what="the following mask redone")
    assert calls[1][1]["mask"]["brightness"] == 0.3
    ctl.desired, ctl.layer = "art", "live"                             # live (layer) counts the same
    m.pushed["devtest"] = {**DIM, "brightness": 0.2}
    await until(lambda: len(calls) == 3, what="redone while live")


async def test_a_running_job_survives_a_following_mask_and_not_a_held_one(rig):
    m, calls = rig.mask, rig.cap.calls
    rig.cap.delay = 0.3
    m.pushed["devtest"] = dict(DIM)
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: len(calls) == 1, what="capture started")
    job = st.job
    m.pushed["devtest"] = {**DIM, "brightness": 0.3}                   # Normal: the job carries on
    await asyncio.sleep(0.1)
    assert st.job is job and not job.done()
    m.suspended = True                                                 # held, and not what the job renders
    await until(lambda: job.done(), what="the stale job dropped")
    assert job.cancelled()
    await until(lambda: st.have is not None, what="the held value committed")
    assert st.have[2] == (60, "#FFC871", 20) and len(calls) == 2


async def test_clip_frames_carry_the_mask_and_blends_use_the_masked_frames(rig):
    m = rig.mask
    m.pushed["devtest"] = {"brightness": 0.55, "warm_hex": "#FFB070", "warm_alpha": 0.3}
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: isinstance(st.committed, laf.VideoFrames), what="decode")
    assert rig.dec.calls[0][4] == {"brightness": 0.55, "warm_hex": "#FFB070", "warm_alpha": 0.3}
    assert all("-m110FFB070060-" in e for _, e in st.committed.frames)
    a, b, _ = rig.dec.blends[0]
    assert a.startswith("s") and b == "s00001.jpg"                    # the decode's (masked) files: masked once
    m.pushed["devtest"] = {"brightness": 0.5, "warm_hex": "#FFB070", "warm_alpha": 0.3}   # Normal moved on
    m.suspended = True
    await until(lambda: st.committed.frames[0][1].count("-m100FFB070060-"), what="re-decoded for the held mask")
    assert len(rig.dec.calls) == 2 and rig.dec.calls[1][4]["brightness"] == 0.5 and len(rig.dec.blends) == 2


async def test_a_still_puts_back_a_clips_mask_only_redecode_on_the_freeze(rig):
    m = rig.mask
    m.pushed["devtest"], m.pushed["north-screen"] = dict(DIM), dict(DIM)
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}       # devtest: a clip
    rig.flask.files["north-screen.jpg"] = {"lm": 1000, "etag": '"n"'}  # north: a still
    await rig.feed.start()
    dev, north = rig.feed.screens["devtest"], rig.feed.screens["north-screen"]
    await until(lambda: isinstance(dev.committed, laf.VideoFrames) and isinstance(north.committed, laf.StillFrame),
                what="both first frames")
    for t in rig.feed._tasks:                                          # drive the ticks by hand, in this order
        t.cancel()
    await asyncio.gather(*rig.feed._tasks, return_exceptions=True)
    m.pushed["devtest"] = m.pushed["north-screen"] = {**DIM, "brightness": 0.3}   # Normal: the sensor moved
    rig.dec.delay = 5.0
    m.suspended = True                                                 # the live mode's first step
    await rig.feed._tick(dev)                                          # the clip first: its re-decode takes the lock
    await until(lambda: len(rig.dec.calls) == 2 and rig.feed._heavy.locked(), what="the re-decode running")
    redecode = dev.job
    await rig.feed._tick(north)                                        # the still goes first
    await until(lambda: north.have[2] == (60, "#FFC871", 20), timeout=1.0, what="the held still")
    assert redecode.cancelled() and rig.dec.cancelled == 1 and dev.have[2] == (80, "#FFC871", 20)
    rig.dec.delay = 0.0
    await rig.feed._tick(dev)                                          # the re-decode is redone
    await until(lambda: dev.have[2] == (60, "#FFC871", 20), what="the clip re-decoded")
    assert len(rig.dec.calls) == 3
    # A clip's new-art decode is never put back.
    rig.flask.files["devtest.mp4"] = {"lm": 3000, "etag": '"m2"'}
    rig.flask.files["north-screen.jpg"] = {"lm": 3000, "etag": '"n2"'}
    rig.dec.delay = 0.3
    await rig.feed._tick(dev)
    await until(lambda: len(rig.dec.calls) == 4 and rig.feed._heavy.locked(), what="the new clip decoding")
    await rig.feed._tick(north)
    assert not dev.job.done() and north.job_key[0] != 'jpg|"n2"'
    await until(lambda: dev.have[0].endswith('"m2"'), what="the new clip decoded")
    assert rig.dec.cancelled == 1


async def test_a_moving_mask_in_normal_keeps_the_failure_backoff(rig):
    m, calls = rig.mask, rig.cap.calls
    rig.cap.fail = True
    m.pushed["devtest"] = dict(DIM)
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:                                 # the sensor steps the mask every 20 ms
        m.pushed["devtest"] = {**DIM, "brightness": 0.4 + 0.01 * ((time.monotonic() - t0) // 0.02 % 50)}
        await asyncio.sleep(0.02)
    n = len(calls)
    assert 1 <= n <= 2 and st.fails == n                               # 0 s, then 1 s (then 3 s): the backoff
    rig.flask.files["devtest.jpg"] = {"lm": 1001, "etag": '"b"'}       # new art: tried at once
    await until(lambda: st.fail_key[0] == 'jpg|"b"', timeout=0.5, what="new art tried")
    assert st.fails == 1 and len(calls) == n + 1


async def test_heavy_work_waits_for_a_cut(rig):
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    other = rig.svc.screens.controllers["north-screen"]
    other.phase = "snapshot"                                           # another screen's cut owns the CPU
    await rig.feed.start()
    await asyncio.sleep(0.2)
    assert rig.cap.calls == [] and rig.feed.screens["devtest"].skips > 0
    assert rig.feed.lookup("devtest").status == "warming"
    other.phase, other._handshake = "live", "exit"                     # a handshake alone also counts
    await asyncio.sleep(0.1)
    assert rig.cap.calls == []
    other._handshake = None
    await until(lambda: len(rig.cap.calls) == 1, what="capture after the cut")


async def test_clip_cycle_clock_and_cut_anchor(rig):
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: isinstance(st.committed, laf.VideoFrames), what="decode")
    assert len(rig.dec.calls) == 1 and rig.dec.calls[0][2] == (640, 360)
    assert 0 in rig.dec.calls[0][1] and 232 in rig.dec.calls[0][1] and len(rig.dec.calls[0][1]) < 34
    v = st.committed
    assert len(v.frames) == 34 and v.frames[32][1] == v.frames[0][1]  # the restart delay shows frame 0 itself
    assert v.frames[20][1] == v.frames[23][1]                          # the hold: one frame, one ETag (304s)
    assert v.frames[25][0].name.startswith("b232-") and v.frames[25][0].exists()   # a Pillow cross-fade blend
    t0 = 5000.0
    st.clocks[v.ident] = t0
    s = rig.feed.lookup("devtest", now=t0 + 10.2)
    assert s.kind == "video" and s.etag == v.frames[10][1] and s.next_s == pytest.approx(0.8, abs=1e-3)
    assert rig.feed.lookup("devtest", now=t0 + CYCLE + 1.5).etag == v.frames[1][1]      # loops at the cycle
    # A cut's anchor for this clip at clip_t = 4.52875 (cycle t = 10): the next request serves sample 10 and the
    # cycle runs on from there.
    key = {"base_url": "/output/devtest.mp4?t=2000000", "kind": "video", "clip_t": 4.52875,
           "mask": laf.NEUTRAL_MASK, "flags": {"nomask": False, "nocolour": False, "fit": "fit"}}
    rig.svc.screens.controllers["devtest"]._shot = (json.dumps(key, sort_keys=True), b"\x89PNG masked", 0.0)
    t1 = 9000.0
    assert rig.feed.lookup("devtest", now=t1).etag == v.frames[10][1]
    assert rig.feed.lookup("devtest", now=t1 + 1.0).etag == v.frames[11][1]
    assert rig.feed.lookup("devtest", now=t1 + 2.0).etag == v.frames[12][1]   # the same shot is not re-applied
    # The cross-fade is ONE blend pass (out of process) of the held frame onto frame 0, its distinct weights only.
    assert len(rig.dec.blends) == 1 and rig.dec.blends[0][1] == "s00001.jpg"
    ws = rig.dec.blends[0][2]
    assert len(ws) == len(set(ws)) == sum(1 for _, w in laf.plan_video(laf.ClipTimeline(D), 24.0, 240)[1] if w > 0)


async def test_job_in_flight_is_cancelled_when_a_cut_starts(rig):
    rig.cap.delay = 0.5
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    await rig.feed.start()
    await until(lambda: len(rig.cap.calls) == 1, what="capture started")
    st = rig.feed.screens["devtest"]
    job = st.job
    other = rig.svc.screens.controllers["north-screen"]
    other._handshake, other.phase = "entry", "freezing"               # another screen's cut begins mid-capture
    await until(lambda: job.done(), timeout=0.4, what="the job cancelled")
    assert job.cancelled() and st.committed is None and st.fails == 0 and st.retry_at == 0.0   # no backoff
    await asyncio.sleep(0.6)                                           # past the old capture's end: nothing ran
    assert len(rig.cap.calls) == 1 and st.committed is None
    other._handshake, other.phase = None, "live"
    await until(lambda: isinstance(st.committed, laf.StillFrame), what="redone after the cut")
    assert len(rig.cap.calls) == 2


async def test_decode_abandoned_when_a_cut_starts_during_it(rig):
    other = rig.svc.screens.controllers["north-screen"]
    rig.dec.on_decode = lambda: setattr(other, "phase", "snapshot")   # the cut starts as the decode pass ends
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: len(rig.dec.calls) == 1 and st.job is not None and st.job.done(), what="decode abandoned")
    assert rig.dec.blends == [] and st.committed is None and st.fails == 0     # no blends, no backoff
    frames = rig.cfg.state_dir / "live_art" / "frames" / "devtest"
    await until(lambda: not frames.exists() or not any(frames.iterdir()), what="its files removed")
    rig.dec.on_decode, other.phase = None, "idle"
    await until(lambda: isinstance(st.committed, laf.VideoFrames), what="decoded after the cut")
    assert len(rig.dec.calls) == 2 and len(rig.dec.blends) == 1


async def test_decode_cancelled_after_its_pass_leaves_no_files(rig):
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m1"'}
    real_head, gate = rig.feed._head, {"hit": 0}

    async def head(sid, ext):
        if inspect.stack()[1].function == "_work" and not gate["hit"]:
            gate["hit"] = 1                                            # the decode is done; a newer clip lands
            rig.flask.files["devtest.mp4"] = {"lm": 3000, "etag": '"m2"'}
            await asyncio.sleep(0.3)                                   # _tick cancels the job here
        return await real_head(sid, ext)

    rig.feed._head = head
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: isinstance(st.committed, laf.VideoFrames) and st.committed.ident.endswith('"m2"'),
                what="the newer clip decoded")
    root = rig.cfg.state_dir / "live_art" / "frames" / "devtest"
    await until(lambda: sorted(p.name for p in root.iterdir()) == sorted(p.name for p in st.gens),
                what="only tracked decodes on disk")
    assert len(rig.dec.calls) == 2


async def test_exit_never_rewinds_the_clip_and_an_entry_reanchors(rig):
    rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}
    ctl = rig.svc.screens.controllers["devtest"]
    await rig.feed.start()
    st = rig.feed.screens["devtest"]
    await until(lambda: isinstance(st.committed, laf.VideoFrames), what="decode")
    v = st.committed
    idx = lambda s: [e for _, e in v.frames].index(s.etag)
    key = {"base_url": "/output/devtest.mp4?t=2000000", "kind": "video", "clip_t": 4.52875,
           "mask": laf.NEUTRAL_MASK, "flags": {"nomask": False, "nocolour": False, "fit": "fit"}}
    ctl._handshake, ctl.phase = "entry", "snapshot"                    # the entry anchors at cycle t = 10
    ctl._shot = (json.dumps(key, sort_keys=True), b"png", 0.0)
    now = rig.feed._now()
    assert idx(rig.feed.lookup("devtest", now=now)) == 10
    ctl._handshake, ctl.phase, ctl.layer = None, "live", "live"
    assert idx(rig.feed.lookup("devtest", now=now + 17.0)) == 27
    # The exit: the page is still held there, the stream still on the glass. The same shot (reused), then a new
    # one (the mask changed) with the same clip_t: neither moves the clock.
    ctl._handshake, ctl.phase = "exit", "freezing"
    await rig.feed._tick(st)
    assert idx(rig.feed.lookup("devtest", now=now + 17.5)) == 27
    ctl.phase = "snapshot"
    ctl._shot = (json.dumps({**key, "mask": {**laf.NEUTRAL_MASK, "brightness": 0.5}}, sort_keys=True), b"png", 1.0)
    await rig.feed._tick(st)
    assert idx(rig.feed.lookup("devtest", now=now + 18.0)) == 28
    ctl._handshake, ctl.phase, ctl.layer = None, "idle", "art"         # back on art
    await rig.feed._tick(st)
    # The next entry re-uses that last shot (an identical state): its start re-arms it as the anchor.
    ctl._handshake, ctl.phase = "entry", "snapshot"
    await rig.feed._tick(st)
    assert idx(rig.feed.lookup("devtest", now=now + 25.0)) == 10


async def test_chrome_start_survives_a_cancelled_capture(tmp_path):
    cap = laf.ChromeArtCapturer(RoomConfig(), tmp_path)
    starts, closed = [], []

    class Browser:
        def is_connected(self):
            return True

        async def close(self):
            closed.append(1)

    def fake_start(c):
        async def start():
            starts.append(1)
            await asyncio.sleep(0.1)
            c._browser = Browser()
            return c._browser
        return start

    cap._start_browser = fake_start(cap)
    t = asyncio.ensure_future(cap._ensure_browser())
    await asyncio.sleep(0.02)
    t.cancel()                                                         # the capture is cancelled mid-launch
    with pytest.raises(asyncio.CancelledError):
        await t
    assert not cap._starting.done()                                    # ... the launch is not
    b = await cap._ensure_browser()                                    # the next capture joins it
    assert starts == [1] and isinstance(b, Browser) and b is cap._browser
    cap2 = laf.ChromeArtCapturer(RoomConfig(), tmp_path)               # close() during a launch: waits, then closes
    cap2._start_browser = fake_start(cap2)
    t = asyncio.ensure_future(cap2._ensure_browser())
    await asyncio.sleep(0.02)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    await cap2.close()
    assert closed == [1] and cap2._browser is None and starts == [1, 1]


async def test_ffmpeg_commands_convert_the_matrix_and_blend_out_of_process(tmp_path, monkeypatch):
    cmds: list[list[str]] = []
    probe = {"streams": [{"avg_frame_rate": "24/1", "nb_frames": "289", "height": 1440, "color_space": "bt709"}],
             "format": {"duration": "12.04"}}

    async def run(name, cmd, timeout_s):
        cmds.append(cmd)
        if name == "ffprobe":
            return json.dumps(probe).encode()
        for i, arg in enumerate(cmd):                                  # write each output: "-f image2 <path>"
            if arg == "image2":
                Image.new("RGB", (4, 4)).save(cmd[i + 1].replace("%05d", "00001"), "JPEG")
        return b""

    monkeypatch.setattr(laf, "_run", run)
    dec = laf.FfmpegDecoder("ffmpeg")
    info = await dec.probe("http://x/output/a.mp4")
    assert info["in_matrix"] == "auto" and info["duration"] == pytest.approx(12.04)   # tagged: read the tag
    probe["streams"][0].pop("color_space")
    assert (await dec.probe("u"))["in_matrix"] == "bt709"                          # untagged HD: BT.709
    probe["streams"][0]["height"] = 480
    assert (await dec.probe("u"))["in_matrix"] == "bt601"                          # untagged SD: BT.601
    await dec.decode("http://x/output/a.mp4", [0, 5], (640, 360), tmp_path, 60, in_matrix="auto")
    vf = cmds[-1][cmds[-1].index("-vf") + 1]
    assert "in_color_matrix=auto:out_color_matrix=bt601:out_range=pc" in vf and vf.endswith("setsar=1,format=yuvj420p")
    await dec.decode("u", [0], (640, 360), tmp_path, 60, mask=laf.NEUTRAL_MASK)
    assert cmds[-1][cmds[-1].index("-vf") + 1] == vf.replace("eq(n,0)+eq(n,5)", "eq(n,0)")   # neutral: as before
    # A mask: the page's per-channel gains on RGB, then back to the JPEG's full-range BT.601.
    await dec.decode("u", [0, 5], (640, 360), tmp_path, 60,
                     mask={"brightness": 0.55, "warm_hex": "#FFB070", "warm_alpha": 0.3})
    mvf = cmds[-1][cmds[-1].index("-vf") + 1]
    assert mvf.endswith("setsar=1,format=rgb24,colorchannelmixer=rr=0.550000:gg=0.498882:bb=0.457471,"
                        "format=yuvj420p")
    assert "in_color_matrix=auto:out_color_matrix=bt601:out_range=pc" in mvf
    outs = [tmp_path / "b1.jpg", tmp_path / "b2.jpg"]
    await dec.blend(tmp_path / "s00001.jpg", tmp_path / "s00002.jpg", [0.25, 0.5], outs, 60)
    cmd = cmds[-1]
    assert "nice" in cmd[0] and cmd[1:3] == ["-n", "19"]                           # niced, out of the room process
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "mix=inputs=2:weights='0.750000 0.250000'[o0]" in fc and "weights='0.500000 0.500000'[o1]" in fc
    assert [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"] == ["[o0]", "[o1]"]
    assert not any("colorchannelmixer" in a for a in cmd)                          # its inputs are masked already


# -- the route --------------------------------------------------------------------------------------------------

async def test_art_route_contract(rig):
    app = web.Application()
    live_art.register(app, rig.svc, feed=rig.feed)
    rig.cap.delay = 0.3
    rig.svc.screens.controllers["devtest"]._shot = ('{"base_url": "/output/devtest.jpg"}', b"\x89PNG masked", 0.0)
    rig.flask.files["devtest.jpg"] = {"lm": 1000, "etag": '"a"'}
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        r = await c.get("/api/v1/screens/devtest/art")
        assert r.status == 503 and r.headers["Retry-After"] == "1" and (await r.json())["error"] == "warming"
        for _ in range(300):
            r = await c.get("/api/v1/screens/devtest/art")
            if r.status == 200:
                break
            await asyncio.sleep(0.01)
        body = await r.read()
        assert r.status == 200 and r.content_type == "image/jpeg" and body.startswith(b"\xff\xd8")
        assert b"masked" not in body                                   # never the cut's (masked) anchor
        assert r.headers["X-Art-Kind"] == "still" and r.headers["Cache-Control"] == "no-store"
        assert "X-Art-Next-S" not in r.headers
        etag = r.headers["ETag"]
        r2 = await c.get("/api/v1/screens/devtest/art", headers={"If-None-Match": etag})
        assert r2.status == 304 and r2.headers["X-Art-Kind"] == "still" and r2.headers["ETag"] == etag
        r3 = await c.get("/api/v1/screens/devtest/art.png")             # the alias
        assert r3.status == 200 and r3.headers["ETag"] == etag
        r = await c.get("/api/v1/screens/nope/art")
        assert r.status == 404 and (await r.json())["error"] == "unknown_screen"
        r = await c.get("/api/v1/screens/north-screen/art")
        assert r.status == 404 and (await r.json())["error"] == "no_art"
        rig.flask.files["devtest.mp4"] = {"lm": 2000, "etag": '"m"'}   # a newer clip
        for _ in range(300):
            r = await c.get("/api/v1/screens/devtest/art")
            if r.headers.get("X-Art-Kind") == "video":
                break
            assert r.status == 200 and r.headers["ETag"] == etag       # the still until the clip is ready
            await asyncio.sleep(0.01)
        assert r.status == 200 and 0 < float(r.headers["X-Art-Next-S"]) <= 1.0
        assert (await r.read()).startswith(b"\xff\xd8")
        r4 = await c.get("/api/v1/screens/devtest/art", headers={"If-None-Match": r.headers["ETag"]})
        assert r4.status in (200, 304) and r4.headers["X-Art-Kind"] == "video" and "X-Art-Next-S" in r4.headers
    finally:
        await c.close()


async def test_route_off_without_a_feed():
    cfg = RoomConfig()
    cfg.snapshot.enabled = False
    svc = SimpleNamespace(cfg=cfg, http=object(), screens=FakeScreens(FakeCtl("devtest")))
    app = web.Application()
    live_art.register(app, svc)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        for _ in range(100):
            r = await c.get("/api/v1/screens/devtest/art")
            if r.status != 503:
                break
            await asyncio.sleep(0.01)
        assert r.status == 404 and "snapshots are off" in (await r.json())["detail"]
    finally:
        await c.close()


async def test_route_warming_until_room_has_built_its_screens():
    svc = SimpleNamespace(cfg=RoomConfig())                            # RoomService before start(): no screens yet
    app = web.Application()
    live_art.register(app, svc)
    c = TestClient(TestServer(app))
    await c.start_server()
    try:
        r = await c.get("/api/v1/screens/devtest/art")
        assert r.status == 503 and r.headers["Retry-After"] == "1" and (await r.json())["error"] == "warming"
        svc.screens = FakeScreens(FakeCtl("devtest"))                  # built: an unknown id is a 404 again
        r = await c.get("/api/v1/screens/nope/art")
        assert r.status == 404 and (await r.json())["error"] == "unknown_screen"
    finally:
        await c.close()


async def test_viewport_is_the_arts_own_shape_at_the_profile_budget():
    # Before any entry has set the screen's art_shape, the feed still sizes frames like the session will be sized:
    # the art's own shape (read from the file) at the profile's pixel count, not the display's configured size.
    from types import SimpleNamespace
    from room_service import live_art_feed as laf

    async def shape_of(state):
        assert state["base_url"].startswith("/output/devtest.jpg")
        return (1080, 1920)                       # a portrait art

    stream = SimpleNamespace(cached_catalog=lambda: {"profiles": {"hevc-2160p30": {"w": 3840, "h": 2160}}})
    ctl = SimpleNamespace(id="devtest", session=None, cfg=SimpleNamespace(profile="hevc-2160p30", width=3840, height=2160),
                          mgr=SimpleNamespace(stream=stream), _art_shape_of=shape_of,
                          _render_size=lambda prof: {"width": 3840, "height": 2160})
    art = SimpleNamespace(base_url="/output/devtest.jpg?t=1")
    assert await laf.art_viewport(ctl, art) == (2160, 3840)

    async def unknown(state):
        return None
    ctl._art_shape_of = unknown                   # no readable file: the screens' own sizing
    assert await laf.art_viewport(ctl, art) == (3840, 2160)
