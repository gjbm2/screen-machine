"""Store.background: the song just sung first, then the ones sung recently (the display page picks among them)."""
from routes.karaoke.store import Store


def ready(st, title, secs=60.0):
    s = st.add_song({"kind": "text", "q": title})
    st.update_song(s["id"], status="ready", audio_s=secs)
    return s["id"]


def test_the_one_just_done_then_the_recent_ones(tmp_path):
    st = Store(tmp_path / "state.json")
    a, b, c, lib = ready(st, "a"), ready(st, "b"), ready(st, "c"), ready(st, "library only")
    for x in (a, b, c):
        st.queue_add(x)
    p = st.s["player"]
    st.tick(p["at"] + 61)                       # a over, b plays
    st.tick(st.s["player"]["at"] + 61)          # b over, c plays
    bg = st.background()
    assert bg[0] == c                           # the one playing / just done leads
    assert bg[1:3] == [b, a]                    # then the recent ones, newest first
    assert lib not in bg                        # two or more recent: only those


def test_nothing_sung_is_the_library(tmp_path):
    st = Store(tmp_path / "state.json")
    a, b = ready(st, "a"), ready(st, "b")
    assert set(st.background()) == {a, b}
