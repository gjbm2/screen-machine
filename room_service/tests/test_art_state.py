"""What a screen shows (Flask's routes/screen_state.py) is passed on to every page of that screen at once, as
`art.state`; only a speed-up, since pages read the document themselves too."""

from __future__ import annotations

from .conftest import Harness

STATE = {"v": 1, "screen": "devtest", "seq": 7, "at": 1.0,
         "still": {"url": "/output/_screens/devtest/000007.jpg", "width": 3840, "height": 2160},
         "clip": None, "transition": None, "video": None}


async def test_every_page_of_the_screen_gets_it(harness: Harness):
    tv = await harness.page()
    viewer = await harness.page(role="viewer")
    status, body = await harness.api("POST", "/api/v1/screens/devtest/art-state", STATE, token=None)
    assert status == 200 and body["sent"] == 2
    for p in (tv, viewer):
        got = await p.wait_for("art.state")
        assert got["seq"] == 7 and got["still"]["url"] == STATE["still"]["url"]


async def test_a_screen_with_no_pages_is_fine(harness: Harness):
    status, body = await harness.api("POST", "/api/v1/screens/the-beast/art-state", STATE, token=None)
    assert status == 200 and body["sent"] == 0


async def test_a_body_that_is_not_a_state_is_refused(harness: Harness):
    status, _ = await harness.api("POST", "/api/v1/screens/devtest/art-state", {"still": {}}, token=None)
    assert status == 400
