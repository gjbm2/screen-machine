"""The overlay action's text is rendered as every instruction's is (now(), _event, the context's vars, the screen's
name), and template code never reaches a screen (27 Nov: the replay's overlay showed its raw {{ }} on both TVs: the
handler rendered with dict_substitute, which has no now())."""
from datetime import datetime

import routes.display as display
import routes.scheduler_handlers as sh


def sent_overlays(monkeypatch):
    sent = []
    monkeypatch.setattr(display, "send_overlay", lambda **kw: sent.append(kw))
    monkeypatch.setattr(sh, "get_qr", lambda **k: "")
    return sent


def overlay(text, ctx=None, dest="north-screen"):
    return sh.handle_overlay({"action": "overlay", "duration": "8s", "text": text}, ctx or {"vars": {}},
                             datetime.now(), [], dest)


def test_now_the_event_the_vars_and_the_screen_name_render(monkeypatch):
    sent = sent_overlays(monkeypatch)
    today = datetime.now().strftime("%Y-%m-%d")
    overlay("{% set d = _event.payload.day %}{{ n }} pictures, {{ 'today' if d == now().strftime('%Y-%m-%d') else d }}"
            " on {{ SCREEN_NAME }}", {"vars": {"n": 8}, "_event": {"payload": {"day": today}}})
    assert len(sent) == 1
    assert sent[0]["substitutions"]["TEXT"] == "8 pictures, today on north-screen"
    assert sent[0]["screens"] == ["north-screen"] and sent[0]["duration"] == 8000


def test_plain_text_goes_up_as_it_is(monkeypatch):
    sent = sent_overlays(monkeypatch)
    overlay("Scheduling will be suspended for 2 hours.")
    assert sent[0]["substitutions"]["TEXT"] == "Scheduling will be suspended for 2 hours."


def test_a_text_that_does_not_render_shows_nothing(monkeypatch):
    sent = sent_overlays(monkeypatch)
    overlay("{{ 1 // 0 }} pictures")
    overlay("{% if %}broken")
    assert sent == []
