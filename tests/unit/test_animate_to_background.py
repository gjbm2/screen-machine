"""A scheduler's `animate image to` makes the transition in the background (25 Nov: made in line, each held the
cloud's loop for minutes and the 'scheduler wedged' alarm went off)."""
import threading
import time
from datetime import datetime

import routes.scheduler_handlers as sh


def test_a_transition_is_made_in_the_background_and_the_instruction_returns_at_once(tmp_path, monkeypatch):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"x"), b.write_bytes(b"x")
    started, release = threading.Event(), threading.Event()

    def slow_commission(x, y):
        started.set()
        release.wait(5)
        return {"idea": "made"}
    import routes.transitions as tr
    monkeypatch.setattr(tr, "commission", slow_commission)
    monkeypatch.setattr(sh, "_generation_refused", lambda *a: False)
    t0 = time.time()
    sh.handle_animate({"image": str(a), "to": str(b)}, {"vars": {}}, datetime.now(), [], "cloud")
    assert time.time() - t0 < 1, "the loop is not held while it is drawn"
    assert started.wait(2), "it is being made"
    release.set()
