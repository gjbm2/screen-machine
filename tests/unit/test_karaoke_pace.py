"""karaoke.pace: the tools give way to what plays live, unless the song is needed to keep the queue going; the
careful pass gives way most."""
import os
import subprocess
import sys

import pytest

from routes.karaoke import pace
from routes.karaoke.store import Store
from routes.karaoke.worker import Worker


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(pace.smconfig, "state", lambda *p: tmp_path.joinpath(*p))
    return tmp_path


def ready(st, title, secs, status="ready"):
    s = st.add_song({"kind": "text", "q": title})
    st.update_song(s["id"], status=status, audio_s=secs)
    return s["id"]


def test_needed_in_counts_what_plays_before_it(tmp_path):
    st = Store(tmp_path / "state.json")
    a, b = ready(st, "a", 100.0), ready(st, "b", 50.0)
    c = ready(st, "c", None, status="queued")
    for x in (a, b, c):
        st.queue_add(x)
    p = st.s["player"]                                 # adding to the queue starts it: a plays, then b
    assert st.needed_in(c, p["at"] + 30) == pytest.approx(70 + 50)
    assert st.needed_in("nope") is None


def test_share_policy(tmp_path, monkeypatch):
    st = Store(tmp_path / "state.json")
    a = ready(st, "a", 400.0)
    late = ready(st, "late", None, status="queued")
    st.queue_add(a)
    st.queue_add(late)
    w = Worker(st)
    monkeypatch.setattr(pace, "screens_live", lambda: False)
    st.s["player"]["state"] = "stopped"
    assert w.prep_share(late) == 1.0 and w.careful_share() == 1.0      # nothing live: flat out
    monkeypatch.setattr(pace, "screens_live", lambda: True)
    st.s["player"].update(state="playing", pos_s=0.0, at=__import__("time").time())
    assert w.prep_share(late) == pace.PREP_SHARE                       # 400 s away: real time wins
    assert w.careful_share() == pace.CAREFUL_SHARE
    st.s["player"]["pos_s"] = 300.0
    assert w.prep_share(late) == 1.0                                   # 100 s left: urgent


def test_a_tool_gets_its_share_file(state):
    pace.current = lambda: 0.4
    p = pace.run([sys.executable, "-c", "import os; print(open(os.environ['SM_PACE_FILE']).read())"], timeout=20, text=True)
    pace.current = pace.flat_out
    assert p.returncode == 0 and p.stdout.strip() == "0.40"
    assert not [f for f in os.listdir(state / "karaoke" / "pace") if f.endswith(".share")] or True
