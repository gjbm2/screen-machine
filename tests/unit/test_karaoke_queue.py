"""The karaoke queue and its player (routes/karaoke/store.py), as the rules it must keep (owner, 28 Nov: "write a proper
set of broad corner & edge cases for queue handling and logic. then test it THOROUGHLY").

The queue is one list in three parts: the songs already sung, the song that is on (playing or paused), the songs to
come. ``view`` reads it as those three, by title. The rules, each with its cases below:

  * A song to come is never among the sung until it has been on; a sung one never comes back by accident.
  * Playing one song moves that one song and nothing else.
  * Songs still being prepared keep their place among the songs to come whatever plays around them.
  * Taking one entry out takes out only that entry.
  * Nothing is dropped above the top of the songs to come.
  * A song the player is waiting for starts when it is ready; a person's Stop calls the waiting off.
  * All of it survives a restart.

The last section runs thousands of random sequences of everything a person, the worker and the clock can do, and
checks the rules after every step.
"""
import json
import os
import random

import pytest

from routes.karaoke.store import Store


# ---------------------------------------------------------------- helpers
def song(st, title, ready=True, secs=100.0):
    s = st.add_song({"kind": "text", "q": title})
    if ready:
        st.update_song(s["id"], status="ready", audio_s=secs)
    return s["id"]


def ready(st, sid, secs=100.0):
    st.update_song(sid, status="ready", audio_s=secs)
    st.tick()


def failed(st, sid):
    st.update_song(sid, status="failed", error="no")
    st.tick()


def view(st):
    """(sung, on, to come), by title."""
    s = st.snapshot()
    q, n, p = s["queue"], s["sung"], s["player"]
    title = {x["id"]: x["title"] for x in s["songs"]}
    names = [title[e["song_id"]] for e in q]
    on = p["state"] in ("playing", "paused")
    return names[:n], (names[n] if on else None), names[n + on:]


def qid(st, title, k=0):
    """The qid of the k-th entry with that title."""
    s = st.snapshot()
    title_of = {x["id"]: x["title"] for x in s["songs"]}
    return [e["qid"] for e in s["queue"] if title_of[e["song_id"]] == title][k]


def state(st):
    return st.snapshot()["player"]["state"]


def finish(st):
    """The song that is on plays out."""
    st.tick()
    p = st.s["player"]
    assert p["state"] == "playing"
    st.tick(p["at"] + 10_000)


def playing(tmp_path, names, unready=()):
    """A store with those songs added in order to an idle player: the first ready one is on."""
    st = Store(tmp_path / "state.json")
    ids = {}
    for n in names.split():
        ids[n] = song(st, n, ready=n not in unready)
        st.queue_add(ids[n])
    return st, ids


def waiting(tmp_path, names, unready=()):
    """A store with those songs to come, nothing on, nothing sung, and the player not about to start by itself."""
    st = Store(tmp_path / "state.json")
    ids = {n: song(st, n, ready=False) for n in names.split()}
    for n in ids:
        st.queue_add(ids[n])
    st.control("stop")
    for n in ids:
        if n not in unready:
            ready(st, ids[n])
    assert view(st) == ([], None, names.split())
    return st, ids


# ---------------------------------------------------------------- adding
def test_the_first_song_added_to_an_idle_player_plays(tmp_path):
    st, _ = playing(tmp_path, "a")
    assert view(st) == ([], "a", []) and state(st) == "playing"


def test_a_song_added_while_one_plays_waits_at_the_end(tmp_path):
    st, ids = playing(tmp_path, "a b")
    st.queue_add(song(st, "c"))
    assert view(st) == ([], "a", ["b", "c"])


def test_up_next_goes_to_the_top_of_the_songs_to_come(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.queue_add(song(st, "d"), next=True)
    assert view(st) == ([], "a", ["d", "b", "c"])
    st.control("stop")
    st.queue_add(song(st, "e"), next=True)               # nothing on: still the top of what is to come
    assert view(st) == (["a"], None, ["e", "d", "b", "c"])


def test_an_index_never_lands_among_the_sung_or_above_the_song_that_is_on(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)                                           # a sung, b on
    for i in (0, 1, -5):
        st.queue_add(song(st, f"x{i}"), index=i)
    sung, on, come = view(st)
    assert sung == ["a"] and on == "b"
    assert come == ["x-5", "x1", "x0", "c"]              # each at the top, never above it
    st.queue_add(song(st, "z"), index=999)
    assert view(st)[2][-1] == "z"


def test_an_index_among_the_songs_to_come_is_kept(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    st.queue_add(song(st, "x"), index=2)                 # whole-queue index 2: between b and c
    assert view(st) == ([], "a", ["b", "x", "c", "d"])


def test_a_song_not_ready_added_to_an_idle_player_plays_when_it_is(tmp_path):
    st, ids = playing(tmp_path, "a", unready="a")
    assert view(st) == ([], None, ["a"])
    ready(st, ids["a"])
    assert view(st) == ([], "a", []) and state(st) == "playing"


def test_adding_after_a_stop_with_songs_waiting_starts_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("stop")
    st.queue_add(song(st, "c"))
    st.tick()
    assert view(st) == (["a"], None, ["b", "c"]) and state(st) == "stopped"


def test_adding_after_a_stop_with_nothing_waiting_plays_it(tmp_path):
    st, _ = playing(tmp_path, "a")
    st.control("stop")
    st.queue_add(song(st, "b"))
    assert view(st) == (["a"], "b", [])


def test_adding_a_ready_song_while_the_others_are_still_being_prepared_keeps_them(tmp_path):
    """Owner, 28 Nov: "I added a new track to queue while others were still encoding, and they disappeared"."""
    st, ids = playing(tmp_path, "a b c", unready=("b", "c"))
    finish(st)                                           # a over; b and c not ready: nothing on
    assert view(st) == (["a"], None, ["b", "c"])
    st.queue_add(song(st, "d"))
    assert view(st) == (["a"], "d", ["b", "c"])          # d plays; b and c are still to come, in order
    ready(st, ids["c"])
    finish(st)
    assert view(st) == (["a", "d"], "c", ["b"])          # c was ready first; b still waits its turn
    ready(st, ids["b"])
    finish(st)
    assert view(st) == (["a", "d", "c"], "b", [])


def test_the_same_song_twice_is_two_entries(tmp_path):
    st, ids = playing(tmp_path, "a b")
    st.queue_add(ids["a"])
    assert view(st) == ([], "a", ["b", "a"])
    st.queue_remove(qid(st, "a", 1))
    assert view(st) == ([], "a", ["b"])                  # the other one is untouched


def test_adding_an_unknown_song_is_refused_and_changes_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b")
    with pytest.raises(KeyError):
        st.queue_add("nope")
    assert view(st) == ([], "a", ["b"])


def test_singers_ride_with_their_entry_wherever_it_goes(tmp_path):
    st, ids = playing(tmp_path, "a b")
    e = st.queue_add(song(st, "c"), singers=["Ann", "Tom"])
    st.play(qid=e["qid"])
    st.queue_move(qid(st, "b"), 99)
    assert next(x for x in st.snapshot()["queue"] if x["qid"] == e["qid"])["singers"] == ["Ann", "Tom"]


# ---------------------------------------------------------------- play this one
def test_play_this_moves_that_one_song_and_nothing_else(tmp_path):
    """Owner, 28 Nov: "'play this' is ONLY meant to move THAT ONE SONG to top of queue not to change anything else"."""
    st, _ = playing(tmp_path, "a b c d e")
    st.play(qid=qid(st, "d"))
    assert view(st) == (["a"], "d", ["b", "c", "e"])
    st.play(qid=qid(st, "e"))
    assert view(st) == (["a", "d"], "e", ["b", "c"])


def test_play_this_from_stopped(tmp_path):
    st, _ = waiting(tmp_path, "a b c")
    st.play(qid=qid(st, "c"))
    assert view(st) == ([], "c", ["a", "b"])


def test_play_this_keeps_songs_still_being_prepared_where_they_are(tmp_path):
    st, _ = playing(tmp_path, "a b c d", unready=("b", "c"))
    st.play(qid=qid(st, "d"))
    assert view(st) == (["a"], "d", ["b", "c"])


def test_playing_a_sung_one_brings_only_it_back(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)
    assert view(st) == (["a", "b"], "c", ["d"])
    st.play(qid=qid(st, "a"))
    assert view(st) == (["b", "c"], "a", ["d"])


def test_playing_a_sung_one_while_nothing_is_on(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    st.control("stop")
    assert view(st) == (["a", "b"], None, ["c"])
    st.play(qid=qid(st, "a"))
    assert view(st) == (["b"], "a", ["c"])


def test_playing_the_one_that_is_on_starts_it_again_and_moves_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    rev = st.s["player"]["rev"]
    st.play(qid=qid(st, "b"))
    assert view(st) == (["a"], "b", ["c"]) and st.s["player"]["rev"] > rev and st.s["player"]["pos_s"] <= 0


def test_play_this_on_a_song_still_being_prepared_waits_at_the_top_then_cuts_in(tmp_path):
    st, ids = playing(tmp_path, "a b c d", unready="d")
    st.play(qid=qid(st, "d"))
    assert view(st) == ([], "a", ["d", "b", "c"]) and state(st) == "playing"      # a carries on
    ready(st, ids["d"])
    assert view(st) == (["a"], "d", ["b", "c"])


def test_a_stop_calls_off_the_song_waiting_to_cut_in_and_it_stays_to_come(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="c")
    st.play(qid=qid(st, "c"))
    st.control("stop")
    ready(st, ids["c"])
    assert view(st) == (["a"], None, ["c", "b"]) and state(st) == "stopped"


def test_next_calls_off_the_song_waiting_to_cut_in(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="c")
    st.play(qid=qid(st, "c"))
    st.control("next")
    assert view(st) == (["a"], "b", ["c"])
    ready(st, ids["c"])
    assert view(st) == (["a"], "b", ["c"])               # b is not cut off


def test_pause_and_seek_leave_the_song_waiting_to_cut_in_waiting(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    st.play(qid=qid(st, "b"))
    st.control("pause")
    st.control("seek", 12.0)
    st.control("resume")
    ready(st, ids["b"])
    assert view(st) == (["a"], "b", [])


def test_a_song_waiting_to_cut_in_that_fails_changes_nothing(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="c")
    st.play(qid=qid(st, "c"))
    failed(st, ids["c"])
    assert view(st) == ([], "a", ["c", "b"]) and state(st) == "playing"
    finish(st)
    assert view(st) == (["a"], "b", ["c"])               # the failed one is passed over and stays


def test_play_this_on_a_song_being_prepared_while_nothing_is_on(tmp_path):
    st, ids = waiting(tmp_path, "a b c", unready="c")
    st.play(qid=qid(st, "c"))
    assert view(st) == ([], None, ["c", "a", "b"])       # it waits at the top; a does not start instead
    ready(st, ids["c"])
    assert view(st) == ([], "c", ["a", "b"])


def test_the_later_of_two_songs_asked_to_cut_in_wins(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready=("b", "c"))
    st.play(qid=qid(st, "b"))
    st.play(qid=qid(st, "c"))
    assert view(st) == ([], "a", ["c", "b"])
    ready(st, ids["b"])
    assert view(st)[1] == "a"                            # b was called off by the later ask
    ready(st, ids["c"])
    assert view(st) == (["a"], "c", ["b"])


def test_playing_a_failed_song_is_refused_and_changes_nothing(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="c")
    failed(st, ids["c"])
    before = (view(st), st.s["player"]["rev"])
    with pytest.raises(LookupError):
        st.play(qid=qid(st, "c"))
    with pytest.raises(LookupError):
        st.play(ids["c"])
    assert (view(st), st.s["player"]["rev"]) == before


def test_playing_an_unknown_entry_or_song_is_refused(tmp_path):
    st, _ = playing(tmp_path, "a b")
    with pytest.raises(KeyError):
        st.play(qid="nope")
    with pytest.raises(KeyError):
        st.play("nope")
    assert view(st) == ([], "a", ["b"])


def test_a_library_song_played_now_is_a_new_entry_at_the_top(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.play(song(st, "x"))
    assert view(st) == (["a"], "x", ["b", "c"])
    st.control("stop")
    st.play(song(st, "y"))
    assert view(st) == (["a", "x"], "y", ["b", "c"])


def test_a_library_song_still_being_prepared_played_now_waits_at_the_top(tmp_path):
    st, _ = playing(tmp_path, "a b")
    x = song(st, "x", ready=False)
    st.play(x)
    assert view(st) == ([], "a", ["x", "b"])
    ready(st, x)
    assert view(st) == (["a"], "x", ["b"])


# ---------------------------------------------------------------- play, pause, next, previous, stop
def test_play_carries_on_with_the_first_ready_song_and_the_others_keep_their_place(tmp_path):
    st, ids = waiting(tmp_path, "a b c d", unready=("a", "b"))
    st.play()
    assert view(st) == ([], "c", ["a", "b", "d"])        # a and b are still to come, not "sung"
    ready(st, ids["a"])
    finish(st)
    assert view(st) == (["c"], "a", ["b", "d"])


def test_play_with_only_songs_being_prepared_says_so_and_they_start_when_ready(tmp_path):
    st, ids = waiting(tmp_path, "a b", unready=("a", "b"))
    with pytest.raises(LookupError, match="isn't ready yet"):
        st.play()
    assert view(st) == ([], None, ["a", "b"])
    ready(st, ids["b"])
    assert view(st) == ([], "b", ["a"])


def test_play_with_nothing_to_come_plays_the_last_one_sung_again(tmp_path):
    st, _ = playing(tmp_path, "a b")
    finish(st)
    finish(st)
    assert view(st) == (["a", "b"], None, [])
    st.play()
    assert view(st) == (["a"], "b", [])


def test_play_with_only_a_failed_song_to_come_plays_the_last_one_sung(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    failed(st, ids["b"])
    finish(st)
    st.play()
    assert view(st) == ([], "a", ["b"])


def test_play_on_an_empty_queue_is_refused(tmp_path):
    st = Store(tmp_path / "state.json")
    with pytest.raises(LookupError):
        st.play()
    assert view(st) == ([], None, [])


def test_play_while_playing_changes_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b")
    rev = st.s["player"]["rev"]
    st.play()
    assert view(st) == ([], "a", ["b"]) and st.s["player"]["rev"] == rev


def test_pause_and_resume_keep_the_song_and_the_queue(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("pause")
    assert view(st) == ([], "a", ["b"]) and state(st) == "paused"
    st.play()                                            # Play resumes
    assert view(st) == ([], "a", ["b"]) and state(st) == "playing"
    st.control("pause")
    st.control("seek", 10_000)
    assert state(st) == "paused" and st.s["player"]["pos_s"] == 100.0     # not past its end


def test_a_paused_song_does_not_end_or_start_another(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("pause")
    st.tick(st.s["player"]["at"] + 10_000)
    assert view(st) == ([], "a", ["b"]) and state(st) == "paused"


def test_next_passes_over_songs_being_prepared_and_they_stay_to_come(tmp_path):
    st, ids = playing(tmp_path, "a b c d", unready=("b", "c"))
    st.control("next")
    assert view(st) == (["a"], "d", ["b", "c"])
    ready(st, ids["b"])
    st.control("next")
    assert view(st) == (["a", "d"], "b", ["c"])


def test_next_on_the_last_ready_song_leaves_nothing_on_and_the_rest_start_when_ready(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    st.control("next")
    assert view(st) == (["a"], None, ["b"]) and state(st) == "stopped"
    ready(st, ids["b"])
    assert view(st) == (["a"], "b", [])


def test_next_with_nothing_on_and_nothing_ready_changes_nothing(tmp_path):
    st, _ = playing(tmp_path, "a")
    finish(st)
    st.control("next")
    st.control("next")
    assert view(st) == (["a"], None, [])


def test_next_while_paused_goes_on_and_plays(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("pause")
    st.control("next")
    assert view(st) == (["a"], "b", []) and state(st) == "playing"


def test_previous_early_in_a_song_brings_back_the_last_one_and_this_one_is_first_to_come(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    assert view(st) == (["a"], "b", ["c"])
    st.control("prev")
    assert view(st) == ([], "a", ["b", "c"])
    finish(st)
    assert view(st) == (["a"], "b", ["c"])               # and on as before


def test_previous_late_in_a_song_starts_it_again(tmp_path):
    st, _ = playing(tmp_path, "a b")
    finish(st)
    st.control("seek", 50.0)
    st.control("prev")
    assert view(st) == (["a"], "b", []) and st.s["player"]["pos_s"] <= 0


def test_previous_with_nothing_sung_starts_the_song_again(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("prev")
    assert view(st) == ([], "a", ["b"]) and state(st) == "playing"


def test_previous_while_nothing_is_on_plays_the_last_one_sung(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    st.control("stop")
    assert view(st) == (["a", "b"], None, ["c"])
    st.control("prev")
    assert view(st) == (["a"], "b", ["c"])


def test_previous_passes_over_a_sung_song_that_is_no_longer_ready(tmp_path):
    st, ids = playing(tmp_path, "a b c")
    finish(st)
    finish(st)
    st.update_song(ids["b"], status="queued")            # being prepared again
    st.control("prev")
    assert view(st) == (["b"], "a", ["c"])               # c back to the top of what is to come; b stays sung


def test_stop_ends_the_song_and_play_goes_on_to_the_next(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.control("stop")
    assert view(st) == (["a"], None, ["b", "c"]) and st.s["player"]["stop"] == "you"
    st.tick()
    assert state(st) == "stopped"                        # it does not start again by itself
    st.play()
    assert view(st) == (["a"], "b", ["c"])


def test_stop_with_nothing_on_changes_nothing_in_the_queue(tmp_path):
    st, _ = waiting(tmp_path, "a b")
    st.control("stop")
    st.control("stop")
    assert view(st) == ([], None, ["a", "b"])


def test_restart_and_seek_with_nothing_on_do_nothing(tmp_path):
    st, _ = waiting(tmp_path, "a b")
    rev = st.s["player"]["rev"]
    st.control("restart")
    st.control("seek", 10.0)
    st.control("pause")
    st.control("resume")
    assert view(st) == ([], None, ["a", "b"]) and st.s["player"]["rev"] == rev


def test_an_unknown_action_does_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b")
    rev = st.s["player"]["rev"]
    st.control("explode")
    assert view(st) == ([], "a", ["b"]) and st.s["player"]["rev"] == rev


# ---------------------------------------------------------------- a song ends
def test_the_queue_plays_through_and_then_it_is_empty(tmp_path):
    """Owner, 28 Nov: "when last song has been played why doesn't queue show as empty???"."""
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    finish(st)
    finish(st)
    assert view(st) == (["a", "b", "c"], None, []) and st.s["player"]["stop"] == "end"
    assert st.now_playing()["queued_after"] == 0


def test_with_play_on_off_each_song_stops_and_play_goes_on(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.settings(autoplay=False)
    finish(st)
    assert view(st) == (["a"], None, ["b", "c"])
    st.tick()
    assert state(st) == "stopped"
    st.play()
    assert view(st) == (["a"], "b", ["c"])


def test_a_song_ends_with_the_next_still_being_prepared_which_starts_when_ready(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    finish(st)
    assert view(st) == (["a"], None, ["b"])
    ready(st, ids["b"])
    assert view(st) == (["a"], "b", []) and state(st) == "playing"


def test_a_song_ends_and_the_one_being_prepared_fails_then_the_one_after_starts_when_ready(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready=("b", "c"))
    finish(st)
    failed(st, ids["b"])
    assert view(st) == (["a"], None, ["b", "c"])
    ready(st, ids["c"])
    assert view(st) == (["a"], "c", ["b"])               # the wait wasn't for b alone


def test_songs_passed_over_at_a_songs_end_play_later_in_their_order(tmp_path):
    st, ids = playing(tmp_path, "a b c d e", unready=("b", "c"))
    finish(st)
    assert view(st) == (["a"], "d", ["b", "c", "e"])
    ready(st, ids["c"])
    ready(st, ids["b"])
    finish(st)
    assert view(st) == (["a", "d"], "b", ["c", "e"])
    finish(st)
    finish(st)
    assert view(st) == (["a", "d", "b", "c"], "e", [])


def test_a_stop_after_the_queue_ran_out_calls_off_the_wait(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    finish(st)
    st.control("stop")
    ready(st, ids["b"])
    assert view(st) == (["a"], None, ["b"]) and state(st) == "stopped"


def test_the_room_leaving_karaoke_calls_off_the_wait(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    finish(st)
    assert st.waiting()
    st.control("stop", by="mode")                        # live_view, when the room's mode leaves karaoke
    assert not st.waiting() and st.s["player"]["stop"] == "mode"
    ready(st, ids["b"])
    assert view(st) == (["a"], None, ["b"]) and state(st) == "stopped"


def test_seeking_past_the_end_ends_the_song_at_the_clocks_next_look(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("seek", 10_000)
    assert view(st) == ([], "a", ["b"])
    st.tick()
    assert view(st) == (["a"], "b", [])


def test_a_song_being_prepared_again_while_it_plays_is_not_cut_off(tmp_path):
    st, ids = playing(tmp_path, "a b")
    st.update_song(ids["a"], status="separating")        # (the worker, at its stems again)
    st.tick()
    assert view(st) == ([], "a", ["b"]) and state(st) == "playing"
    assert view(Store(st.path)) == ([], "a", ["b"])


def test_a_song_does_not_end_before_its_time(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.tick(st.s["player"]["at"] + 50)
    assert view(st) == ([], "a", ["b"])


# ---------------------------------------------------------------- taking out
def test_taking_out_a_song_to_come_takes_only_it(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    st.queue_remove(qid(st, "c"))
    assert view(st) == ([], "a", ["b", "d"])


def test_taking_out_a_sung_song_takes_only_it(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)
    st.queue_remove(qid(st, "a"))
    assert view(st) == (["b"], "c", ["d"]) and state(st) == "playing"


def test_taking_out_the_one_playing_goes_on_to_the_next_ready_one(tmp_path):
    st, _ = playing(tmp_path, "a b c d", unready="b")
    st.queue_remove(qid(st, "a"))
    assert view(st) == ([], "c", ["b", "d"])             # a is gone, not "sung"; b keeps its place


def test_taking_out_the_one_playing_with_nothing_ready_keeps_the_sung_sung(tmp_path):
    """Owner, 28 Nov: "I removed last song from queue, and all my old songs got added back into the queue"."""
    st, ids = playing(tmp_path, "a b c d", unready="d")
    finish(st)
    finish(st)
    st.queue_remove(qid(st, "c"))
    assert view(st) == (["a", "b"], None, ["d"]) and state(st) == "stopped"
    ready(st, ids["d"])
    assert view(st) == (["a", "b"], "d", [])             # it was playing: what was being prepared goes on


def test_taking_out_the_last_song_after_it_played_keeps_the_others_sung(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    finish(st)
    finish(st)
    st.queue_remove(qid(st, "c"))
    assert view(st) == (["a", "b"], None, [])
    st.queue_remove(qid(st, "a"))
    assert view(st) == (["b"], None, [])
    st.queue_remove(qid(st, "b"))
    assert view(st) == ([], None, [])


def test_taking_out_a_paused_song_leaves_nothing_on_and_starts_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    st.control("pause")
    st.queue_remove(qid(st, "b"))
    st.tick()
    assert view(st) == (["a"], None, ["c"]) and state(st) == "stopped"


def test_taking_out_an_unknown_entry_changes_nothing(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.queue_remove("nope")
    assert view(st) == ([], "a", ["b"])


def test_taking_out_the_song_waiting_to_cut_in_calls_it_off(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    st.play(qid=qid(st, "b"))
    st.queue_remove(qid(st, "b"))
    ready(st, ids["b"])
    assert view(st) == ([], "a", [])


def test_taking_everything_out_one_by_one_in_any_order(tmp_path):
    for seed in range(20):
        st, _ = playing(tmp_path / str(seed), "a b c d e", unready="d")
        finish(st)
        qids = [e["qid"] for e in st.snapshot()["queue"]]
        random.Random(seed).shuffle(qids)
        for q in qids:
            st.queue_remove(q)
            st.tick()
        assert view(st) == ([], None, []) and state(st) == "stopped"


def test_deleting_a_song_from_the_library_takes_out_each_of_its_entries_and_nothing_else(tmp_path):
    st, ids = playing(tmp_path, "a b c")
    st.queue_add(ids["a"])
    finish(st)                                           # a sung, b on, to come: c, a
    st.remove_song(ids["a"])
    assert view(st) == ([], "b", ["c"]) and state(st) == "playing"
    st.remove_song(ids["b"])                             # the one playing: on to c
    assert view(st) == ([], "c", [])
    st.remove_song(ids["c"])
    assert view(st) == ([], None, []) and st.snapshot()["songs"] == []


def test_deleting_the_song_the_background_music_starts_with(tmp_path):
    st, ids = playing(tmp_path, "a b")
    finish(st)
    finish(st)
    st.remove_song(ids["b"])
    assert ids["b"] not in st.background() and view(st) == (["a"], None, [])


def test_a_song_that_turned_out_to_be_another_keeps_its_place(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="c")
    st.merge_into(ids["c"], ids["b"])                    # c was b all along
    assert view(st) == ([], "a", ["b", "b"])


# ---------------------------------------------------------------- moving
def test_reordering_the_songs_to_come(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    st.queue_move(qid(st, "d"), 1)
    assert view(st) == ([], "a", ["d", "b", "c"])
    st.queue_move(qid(st, "d"), 3)
    assert view(st) == ([], "a", ["b", "c", "d"])
    st.queue_move(qid(st, "b"), 2)
    assert view(st) == ([], "a", ["c", "b", "d"])


def test_a_song_dropped_above_the_one_playing_goes_to_the_top_of_what_is_to_come(tmp_path):
    """Owner, 28 Nov: "dragging queued song 2 up to top place: it disappeared"."""
    st, _ = playing(tmp_path, "a b c d")
    finish(st)                                           # a sung, b on
    for i in (0, 1, -3):
        st.queue_move(qid(st, "d"), i)
        assert view(st) == (["a"], "b", ["d", "c"])


def test_a_song_dropped_above_the_top_while_nothing_is_on(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    finish(st)
    st.control("stop")                                   # a, b sung
    st.queue_move(qid(st, "d"), 0)
    assert view(st) == (["a", "b"], None, ["d", "c"])


def test_the_song_that_is_on_does_not_move(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    for i in (0, 2, 99):
        st.queue_move(qid(st, "b"), i)
        assert view(st) == (["a"], "b", ["c"]) and state(st) == "playing"


def test_a_sung_song_dropped_among_the_songs_to_come_is_to_come_again(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)                                           # a, b sung; c on; d to come
    st.queue_move(qid(st, "a"), 3)
    assert view(st) == (["b"], "c", ["d", "a"])
    st.queue_move(qid(st, "b"), 1)                       # dropped on the one playing: the top of what is to come
    assert view(st) == ([], "c", ["b", "d", "a"])


def test_a_sung_song_moved_among_the_sung_stays_sung(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)
    finish(st)                                           # a, b, c sung; d on
    st.queue_move(qid(st, "c"), 0)
    assert view(st) == (["c", "a", "b"], "d", [])
    st.queue_move(qid(st, "c"), 2)
    assert view(st) == (["a", "b", "c"], "d", [])


def test_moving_past_the_end_lands_at_the_end(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.queue_move(qid(st, "b"), 999)
    assert view(st) == ([], "a", ["c", "b"])


def test_moving_an_unknown_entry_is_refused(tmp_path):
    st, _ = playing(tmp_path, "a b")
    with pytest.raises(KeyError):
        st.queue_move("nope", 0)
    assert view(st) == ([], "a", ["b"])


def test_moving_songs_does_not_disturb_the_one_playing(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    rev = st.s["player"]["rev"]
    st.queue_move(qid(st, "d"), 1)
    st.queue_move(qid(st, "b"), 3)
    assert st.s["player"]["rev"] == rev and state(st) == "playing"


# ---------------------------------------------------------------- clear, a new session
def test_clear_keeps_only_the_song_playing(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)
    st.queue_clear()
    assert view(st) == ([], "b", []) and state(st) == "playing"
    finish(st)
    assert view(st) == (["b"], None, [])


def test_clear_with_nothing_on_empties_everything_and_the_next_song_added_plays(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.control("stop")
    st.queue_clear()
    assert view(st) == ([], None, [])
    st.queue_add(song(st, "x"))
    assert view(st) == ([], "x", [])


def test_clear_keeps_a_paused_song(tmp_path):
    st, _ = playing(tmp_path, "a b")
    st.control("pause")
    st.queue_clear()
    assert view(st) == ([], "a", []) and state(st) == "paused"


def test_a_new_session_drops_the_sung_and_keeps_the_rest(tmp_path):
    st, _ = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)
    st.new_session()
    assert view(st) == ([], "c", ["d"]) and state(st) == "playing"
    finish(st)
    finish(st)
    st.new_session()
    assert view(st) == ([], None, [])


def test_a_new_session_after_a_stop_keeps_the_songs_to_come(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.control("stop")
    st.new_session()
    assert view(st) == ([], None, ["b", "c"])
    st.play()
    assert view(st) == ([], "b", ["c"])


# ---------------------------------------------------------------- what the pages are told
def test_the_pages_are_told_how_many_are_sung_and_which_entry_the_player_is_on(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    s = st.snapshot()
    assert s["sung"] == 0 and s["player"]["qid"] == s["queue"][0]["qid"]
    finish(st)
    s = st.snapshot()
    assert s["sung"] == 1 and s["player"]["qid"] == s["queue"][1]["qid"]
    st.control("stop")
    s = st.snapshot()                                    # nothing on: the last one sung, for pages that go by the qid
    assert s["sung"] == 2 and s["player"]["qid"] == s["queue"][1]["qid"] and s["player"]["song_id"] is None
    st.queue_clear()
    assert st.snapshot()["player"]["qid"] is None


def test_the_screens_are_told_what_is_next_and_how_many_wait(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="b")
    st.queue_singers(qid(st, "a"), ["Ann"])
    st.queue_singers(qid(st, "c"), ["Tom"])
    n = st.now_playing()
    assert n["song"]["title"] == "a" and n["singers"] == ["Ann"]
    assert n["up_next"]["title"] == "c" and n["up_next"]["singers"] == ["Tom"]     # the first one ready
    assert n["queued_after"] == 2 and n["background"] == []
    st.control("stop")
    n = st.now_playing()
    assert n["song"] is None and n["singers"] == [] and n["up_next"]["title"] == "c" and n["queued_after"] == 2


def test_the_background_music_is_the_songs_sung_newest_first(tmp_path):
    st, ids = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)
    st.control("stop")                                   # a, b, c sung (c last on)
    bg = st.background()
    assert bg[:3] == [ids["c"], ids["b"], ids["a"]] and ids["d"] not in bg[:3]


# ---------------------------------------------------------------- a restart
def reopen(st):
    return Store(st.path)


def test_a_restart_mid_song_carries_on_with_the_same_queue(tmp_path):
    st, _ = playing(tmp_path, "a b c d", unready="d")
    finish(st)
    st2 = reopen(st)
    assert view(st2) == (["a"], "b", ["c", "d"]) and state(st2) == "playing"
    assert st2.s["player"]["rev"] > st.s["player"]["rev"]                 # the pages place the sound again


def test_a_restart_while_paused_or_stopped(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    st.control("pause")
    assert view(reopen(st)) == ([], "a", ["b", "c"]) and state(reopen(st)) == "paused"
    st.control("stop")
    st2 = reopen(st)
    assert view(st2) == (["a"], None, ["b", "c"]) and st2.s["player"]["stop"] == "you"
    st2.tick()
    assert state(st2) == "stopped"


def test_a_song_the_player_waits_for_still_starts_after_a_restart(tmp_path):
    st, ids = playing(tmp_path, "a b", unready="b")
    finish(st)
    st2 = reopen(st)
    ready(st2, ids["b"])
    assert view(st2) == (["a"], "b", [])


def test_a_song_waiting_to_cut_in_still_does_after_a_restart(tmp_path):
    st, ids = playing(tmp_path, "a b c", unready="c")
    st.play(qid=qid(st, "c"))
    st2 = reopen(st)
    assert view(st2) == ([], "a", ["c", "b"])
    ready(st2, ids["c"])
    assert view(st2) == (["a"], "c", ["b"])


def test_a_song_that_ran_out_while_the_machine_was_off_is_still_to_come(tmp_path):
    st, _ = playing(tmp_path, "a b c")
    finish(st)                                           # a sung, b on
    with st.lock:
        st.s["player"]["at"] -= 10_000                   # the machine was off for hours
        st.commit()
    st2 = reopen(st)
    assert view(st2) == (["a"], None, ["b", "c"]) and st2.s["player"]["stop"] == "boot"
    st2.tick()
    assert state(st2) == "stopped"                       # it does not start by itself
    st2.play()
    assert view(st2) == (["a"], "b", ["c"])


def test_a_song_deleted_behind_the_stores_back_takes_its_entries_and_the_count_follows(tmp_path):
    st, ids = playing(tmp_path, "a b c d")
    finish(st)
    finish(st)                                           # a, b sung; c on
    d = json.loads(st.path.read_text())
    del d["songs"][ids["a"]]
    st.path.write_text(json.dumps(d))
    assert view(reopen(st)) == (["b"], "c", ["d"])
    del d["songs"][ids["c"]]                             # the one that was on
    st.path.write_text(json.dumps(d))
    st2 = reopen(st)
    assert view(st2) == (["b"], None, ["d"]) and state(st2) == "stopped"


@pytest.mark.parametrize("player, want", [
    ({"state": "playing", "qid": 1}, (["a"], "b", ["c"])),
    ({"state": "paused", "qid": 1}, (["a"], "b", ["c"])),
    ({"state": "stopped", "qid": 1, "stop": "end"}, (["a", "b"], None, ["c"])),
    ({"state": "stopped", "qid": 1, "stop": "you"}, (["a", "b"], None, ["c"])),
    ({"state": "stopped", "qid": 1, "stop": "boot"}, (["a"], None, ["b", "c"])),
    ({"state": "stopped", "qid": None}, ([], None, ["a", "b", "c"])),
    ({"state": "stopped", "qid": "gone"}, ([], None, ["a", "b", "c"])),
    ({"state": "playing", "qid": "gone"}, ([], None, ["a", "b", "c"])),
])
def test_a_file_from_before_the_count_was_kept_is_read_by_the_players_entry(tmp_path, player, want):
    st, _ = waiting(tmp_path, "a b c")
    d = json.loads(st.path.read_text())
    d.pop("sung")
    q = d["queue"]
    k = player["qid"]
    d["player"] = {**d["player"], **player, "qid": q[k]["qid"] if isinstance(k, int) else k,
                   "song_id": q[k]["song_id"] if isinstance(k, int) else None, "pos_s": 1.0}
    import time
    d["player"]["at"] = time.time()
    st.path.write_text(json.dumps(d))
    assert view(reopen(st)) == want


def test_a_file_from_before_with_a_song_waiting_to_start(tmp_path):
    st, ids = waiting(tmp_path, "a b", unready=("a", "b"))
    d = json.loads(st.path.read_text())
    d.pop("sung")
    d.pop("play_now", None)
    d["autostart"] = d["queue"][0]["qid"]                # the old way: the entry's qid
    st.path.write_text(json.dumps(d))
    st2 = reopen(st)
    ready(st2, ids["a"])
    assert view(st2) == ([], "a", ["b"])


# ---------------------------------------------------------------- thousands of random sequences
class Model:
    """The rules again, written the plainest way: three separate lists of qids, and no shared code with the store.
    The random runs do everything to both and the two must agree after every step."""

    def __init__(self):
        self.sung: list[str] = []
        self.on: str | None = None
        self.come: list[str] = []
        self.state = "stopped"
        self.auto = False                                # nothing on: the first ready song to come plays
        self.cut: str | None = None                      # Play now on one being prepared: it plays when ready
        self.song: dict[str, str] = {}                   # qid -> song id
        self.status: dict[str, str] = {}                 # song id -> queued | ready | failed
        self.autoplay = True
        self.late = False                                # more than 3 s into the song (Previous starts it again)

    def parts(self):
        return self.sung, self.on, self.come

    def is_ready(self, q):
        return self.status[self.song[q]] == "ready"

    def first_ready(self):
        return next((q for q in self.come if self.is_ready(q)), None)

    def drop(self, q):
        for part in (self.sung, self.come):
            if q in part:
                part.remove(q)
        if self.on == q:
            self.on = None

    def start(self, q):
        """q goes on; the one that was on is sung; nothing else moves."""
        if self.on and self.on != q:
            self.sung.append(self.on)
        self.drop(q)
        self.on, self.state, self.late = q, "playing", False

    def halt(self):
        if self.on:
            self.sung.append(self.on)
        self.on, self.state = None, "stopped"

    def look(self):
        """The clock's look: the songs waited for, if ready."""
        if self.cut:
            st = self.status.get(self.song.get(self.cut, ""))
            if st in (None, "failed"):
                self.cut = None
            elif st == "ready":
                q, self.cut, self.auto = self.cut, None, False
                self.start(q)
        if self.auto:
            if self.on:
                self.auto = False
            elif self.first_ready():
                self.auto = False
                self.start(self.first_ready())

    def top(self):
        return len(self.sung) + (1 if self.on else 0)

    def add(self, q, sid, where):
        self.song[q] = sid
        if not self.on and not self.come:
            self.auto = True
        if where == "end":
            self.come.append(q)
        elif where == "next":
            self.come.insert(0, q)
        else:                                            # an index of the whole queue: never above the top
            self.come.insert(max(0, min(len(self.come), where - self.top())), q)
        self.look()

    def play_qid(self, q):
        if self.status[self.song[q]] == "failed":
            raise LookupError
        self.auto, self.cut = False, None
        if self.is_ready(q):
            self.start(q)
        else:
            if q != self.on:
                self.drop(q)
                self.come.insert(0, q)
            self.cut = q

    def play_sid_refused(self, sid):
        return self.status[sid] == "failed"

    def play_sid(self, q, sid):
        self.song[q] = sid
        self.come.insert(0, q)
        self.play_qid(q)

    def play(self):
        if self.state == "playing":
            return
        if self.state == "paused":
            self.state = "playing"
            return
        q = self.first_ready()
        if q is None:
            if any(self.status[self.song[x]] == "queued" for x in self.come):
                self.auto = True
                raise LookupError
            q = self.sung[-1] if self.sung and self.is_ready(self.sung[-1]) else None
        if q is None:
            raise LookupError
        self.auto, self.cut = False, None
        self.start(q)

    def control(self, action):
        if action == "pause" and self.state == "playing":
            self.state = "paused"
        elif action == "resume" and self.state == "paused":
            self.state = "playing"
        elif action == "restart" and self.on:
            self.auto, self.cut = False, None
            self.start(self.on)
        elif action == "stop":
            self.auto, self.cut = False, None
            self.halt()
        elif action == "next":
            self.cut = None
            q = self.first_ready()
            self.auto = q is None
            if q:
                self.start(q)
            else:
                self.halt()
        elif action == "prev":
            prv = next((q for q in reversed(self.sung) if self.is_ready(q)), None)
            if self.on and (self.late or prv is None):
                self.auto, self.cut = False, None
                self.start(self.on)
            elif prv:
                self.auto, self.cut = False, None
                if self.on:
                    self.come.insert(0, self.on)         # the one that was on: the first to come again
                    self.on = None
                self.start(prv)

    def seek(self, pos):
        if self.on:
            self.late = pos > 3

    def finish(self):
        """The song that is on plays out."""
        q = self.first_ready() if self.autoplay else None
        if q:
            self.start(q)
        else:
            self.halt()
            self.auto = self.autoplay

    def remove(self, q):
        if q == self.on:
            nxt = self.first_ready() if self.state == "playing" else None
            was, self.on = self.state, None
            if nxt:
                self.start(nxt)
            else:
                self.state = "stopped"
                self.auto = was == "playing" and self.autoplay
        else:
            self.drop(q)
        if self.cut == q:
            self.cut = None

    def delete_song(self, sid):
        for q in [q for q in self.sung + ([self.on] if self.on else []) + self.come if self.song[q] == sid]:
            self.remove(q)
        del self.status[sid]

    def move(self, q, index):
        if q == self.on:
            return
        total = len(self.sung) + len(self.come) + (1 if self.on else 0)
        t = max(0, min(total - 1, index))                # where it lands, in the whole queue
        if q in self.sung and t < len(self.sung):
            self.sung.remove(q)
            self.sung.insert(t, q)                       # among the sung still
            return
        self.drop(q)
        self.come.insert(max(0, t - self.top()), q)      # to come, never above the top
        self.look()

    def clear(self):
        self.sung, self.come, self.auto, self.cut = [], [], False, None

    def new_session(self):
        self.sung = []


def parts(st):
    """(sung qids, the qid that is on or None, qids to come). Read straight from the store: the random runs call it
    tens of thousands of times."""
    q, n, p = [e["qid"] for e in st.s["queue"]], st.s["sung"], st.s["player"]
    on = p["state"] in ("playing", "paused")
    return q[:n], (q[n] if on else None), q[n + on:]


def check(st, ever, looked=False):
    """What is true after every step, whatever was done (``looked``: and after the clock's next look)."""
    q, n, p = st.s["queue"], st.s["sung"], st.s["player"]
    qids = [e["qid"] for e in q]
    status = {sid: x["status"] for sid, x in st.s["songs"].items()}
    assert isinstance(n, int) and 0 <= n <= len(q)
    assert len(set(qids)) == len(qids), "an entry twice"
    assert all(e["song_id"] in status for e in q), "an entry without its song"
    assert p["state"] in ("playing", "paused", "stopped")
    if p["state"] != "stopped":
        assert n < len(q) and p["qid"] == q[n]["qid"] and p["song_id"] == q[n]["song_id"], "the player isn't on queue[sung]"
        assert status[p["song_id"]] == "ready", "a song that isn't ready is on"
        ever.add(p["qid"])
    else:
        assert p["qid"] == (q[n - 1]["qid"] if n else None) and p["song_id"] is None
    assert set(qids[:n]) <= ever, "a song that was never on is among the sung"
    assert st._cut is None or st._cut in qids
    if looked and p["state"] == "stopped" and st._auto:
        assert st._first_ready() is None, "the player waits while a song is ready"
    assert len(st.to_come()) == len(q) - n - (p["state"] != "stopped")


def check_pages(st):
    """What the pages are sent agrees with the store, and is plain JSON."""
    s = json.loads(json.dumps(st.snapshot()))
    assert s["sung"] == st.s["sung"] and [e["qid"] for e in s["queue"]] == [e["qid"] for e in st.s["queue"]]
    assert s["player"]["qid"] == st.s["player"]["qid"] and s["player"]["state"] == st.s["player"]["state"]
    n = json.loads(json.dumps(st.now_playing()))
    assert n["queued_after"] == len(st.to_come())
    first = st._first_ready()
    assert (n["up_next"] or {}).get("id") == (first["song_id"] if first else None)
    assert (n["song"] or {}).get("id") == st.s["player"]["song_id"]


def run_sequence(tmp_path, seed, steps):
    rng = random.Random(seed)
    st = Store(tmp_path / "state.json")
    st._save = lambda: None                              # in memory, for speed; a restart saves for real
    m = Model()
    ever: set[str] = set()                               # every entry that has been on
    here: set[str] = set()                               # every entry that should be in the queue
    n_songs = 0
    log = []

    def agree(what):
        assert parts(st) == m.parts(), f"{what}: the store has {parts(st)}, the rules say {m.parts()}"
        assert st.s["player"]["state"] == m.state, f"{what}: {st.s['player']['state']}, the rules say {m.state}"
        assert (st._auto, st._cut) == (m.auto, m.cut), f"{what}: waiting {(st._auto, st._cut)}, the rules say {(m.auto, m.cut)}"

    def entries():
        return [e["qid"] for e in st.s["queue"]]

    def songs(pred=lambda s: True):
        return [sid for sid, s in st.s["songs"].items() if pred(s)]

    def new_song():
        nonlocal n_songs
        n_songs += 1
        is_ready = rng.random() < 0.6
        sid = song(st, f"s{n_songs}", ready=is_ready)
        m.status[sid] = "ready" if is_ready else "queued"
        return sid

    def set_status(sid, status):
        st.update_song(sid, status=status, **({"audio_s": 100.0} if status == "ready" else {}))
        m.status[sid] = status

    for step in range(steps):
        if step % 25 == 0:
            check_pages(st)
        sung0, on0, come0 = parts(st)
        rev0 = st.s["player"]["rev"]
        op = rng.choices(
            ["add", "add_next", "add_at", "play_qid", "play_sid", "play", "pause", "resume", "stop", "next", "prev",
             "restart", "seek", "finish", "tick", "ready", "fail", "retry", "remove", "move", "delete_song", "clear",
             "new_session", "reopen", "autoplay", "singers"],
            [10, 3, 3, 8, 3, 5, 2, 2, 4, 6, 3, 1, 1, 10, 4, 10, 2, 1, 6, 8, 1, 0.4, 0.4, 2, 0.5, 1])[0]
        target = None
        refused = wanted = False                         # the store said no; the rules say it should
        try:
            if op in ("add", "add_next", "add_at"):
                sid = rng.choice(songs()) if songs() and rng.random() < (0.4 if n_songs < 40 else 0.97) else new_song()
                i = rng.randint(-2, len(entries()) + 2)
                e = st.queue_add(sid, index=i if op == "add_at" else None, next=op == "add_next")
                here.add(e["qid"])
                target = e["qid"]
                m.add(target, sid, "end" if op == "add" else "next" if op == "add_next" else i)
            elif op == "play_qid" and entries():
                target = rng.choice(entries())
                try:
                    m.play_qid(target)
                except LookupError:
                    wanted = True
                st.play(qid=target)
            elif op == "play_sid":
                sid = rng.choice(songs()) if songs() and rng.random() < (0.5 if n_songs < 40 else 0.97) else new_song()
                wanted = m.play_sid_refused(sid)
                st.play(sid)
                new = set(entries()) - here
                assert len(new) == 1
                target = new.pop()
                here.add(target)
                m.play_sid(target, sid)
            elif op == "play":
                try:
                    m.play()
                except LookupError:
                    wanted = True
                st.play()
            elif op in ("pause", "resume", "stop", "next", "prev", "restart"):
                st.control(op)
                m.control(op)
            elif op == "seek":
                pos = rng.choice([rng.uniform(-5, 2), rng.uniform(4, 95)])     # (not within a breath of the 3 s mark)
                st.control("seek", pos)
                m.seek(pos)
            elif op == "finish":
                st.tick()
                m.look()
                check(st, ever)
                agree("before the song ends")
                sung0, on0, come0 = parts(st)
                if st.s["player"]["state"] == "playing":
                    st.tick(st.s["player"]["at"] + 10_000)
                    m.finish()
            elif op == "tick":
                st.tick()
                m.look()
            elif op == "ready" and songs(lambda s: s["status"] == "queued"):
                set_status(rng.choice(songs(lambda s: s["status"] == "queued")), "ready")
            elif op == "fail" and songs(lambda s: s["status"] == "queued"):
                set_status(rng.choice(songs(lambda s: s["status"] == "queued")), "failed")
            elif op == "retry" and songs(lambda s: s["status"] == "failed"):
                set_status(rng.choice(songs(lambda s: s["status"] == "failed")), "queued")
            elif op == "remove" and entries():
                target = rng.choice(entries())
                st.queue_remove(target)
                here.discard(target)
                m.remove(target)
            elif op == "move" and entries():
                target = rng.choice(entries())
                i = rng.randint(-2, len(entries()) + 2)
                st.queue_move(target, i)
                m.move(target, i)
            elif op == "delete_song" and songs():
                sid = rng.choice(songs())
                here -= {e["qid"] for e in st.s["queue"] if e["song_id"] == sid}
                st.remove_song(sid)
                m.delete_song(sid)
            elif op == "clear":
                st.queue_clear()
                here = {on0} if on0 else set()
                m.clear()
            elif op == "new_session":
                st.new_session()
                here -= set(sung0)
                m.new_session()
            elif op == "reopen":
                Store._save(st)
                st = Store(st.path)
                st._save = lambda: None
            elif op == "autoplay":
                m.autoplay = rng.random() < 0.7
                st.settings(autoplay=m.autoplay)
            elif op == "singers" and entries():
                st.queue_singers(rng.choice(entries()), [rng.choice(["Ann", "Tom", "Bea"])])
        except LookupError as e:
            assert op in ("play", "play_qid", "play_sid") and not isinstance(e, KeyError), (op, e)
            refused = True
            if op == "play_sid":
                assert set(entries()) == here, "a refused Play now left an entry behind"
        log.append((op, target))
        try:
            assert refused == wanted, f"the store {'refused' if refused else 'allowed'} it; the rules say otherwise"
            for again in (False, True):                  # after the step, and after the clock's next look
                if again:
                    sung0, on0, come0 = parts(st)
                    rev0 = st.s["player"]["rev"]
                    op, target = "tick", None
                    st.tick()
                    m.look()
                check(st, ever, looked=again)
                agree("after the clock's look" if again else "after the step")
                sung, on, come = parts(st)
                assert st.s["player"]["rev"] >= rev0
                assert set(sung) | set(come) | ({on} if on else set()) == here, "an entry appeared or vanished"
                # Only the song that was on joins the sung, and only by having been on.
                assert set(sung) - set(sung0) <= {on0}, "a song joined the sung without being the one on"
                if op not in ("move",):
                    kept = [x for x in sung0 if x in sung]
                    assert [x for x in sung if x in sung0] == kept, "the sung changed order"
                # Nothing leaves the sung but the one asked for (played, moved, taken out), Previous's and Play's one.
                left = set(sung0) - set(sung)
                if op in ("clear", "new_session", "delete_song"):
                    pass
                elif op in ("prev", "play"):
                    assert left <= {on}, "a sung song came back unasked"
                else:
                    assert left <= {target}, "a sung song came back unasked"
                # The songs to come keep their order; only the one asked for, or the one that goes on, moves.
                if op not in ("clear", "delete_song"):
                    movers = {target, on, on0}
                    a = [x for x in come0 if x not in movers and x in come]
                    b = [x for x in come if x not in movers and x in come0]
                    assert a == b, "the songs to come changed order"
                    gone = set(come0) - set(come) - {on}
                    assert gone <= ({target} if op in ("remove", "move", "play_qid") else set()), \
                        "a song to come vanished"
                    came = set(come) - set(come0)
                    assert came <= {target, on0}, "a song joined the songs to come unasked"
                if op in ("pause", "resume", "seek", "tick", "ready", "fail", "retry", "autoplay", "singers", "move",
                          "reopen") and on0 and target != on0:
                    assert on == on0 or (op in ("tick", "ready") and on != on0 and on0 in sung), \
                        "the song that was on was disturbed"
        except AssertionError as e:
            raise AssertionError(f"seed {seed}, after {log[-12:]}: {e}") from e
    return st


#: How many random sequences each run plays (KARAOKE_QUEUE_SEEDS=20000 for a deep run after a change to the queue).
SEEDS = int(os.environ.get("KARAOKE_QUEUE_SEEDS", "300"))


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_sequences_keep_the_rules(tmp_path, seed):
    run_sequence(tmp_path, seed, 150)


@pytest.mark.parametrize("seed", range(max(20, SEEDS // 100)))
def test_long_random_sequences_keep_the_rules(tmp_path, seed):
    run_sequence(tmp_path, 1_000_000 + seed, 2000)


@pytest.mark.parametrize("seed", range(max(40, SEEDS // 20)))
def test_short_random_sequences_on_a_tiny_library_keep_the_rules(tmp_path, seed, monkeypatch):
    """Few songs, so the same song is queued many times over and deleted from under its entries."""
    monkeypatch.setattr(random.Random, "choices", _few_new_songs(random.Random.choices))
    run_sequence(tmp_path, 2_000_000 + seed, 300)


def _few_new_songs(choices):
    def pick(self, population, weights=None, **kw):
        if weights and "delete_song" in population:      # the step picker: far more deletes, clears and restarts
            weights = [w * (8 if op in ("delete_song", "clear", "new_session", "reopen", "retry", "fail") else 1)
                       for op, w in zip(population, weights)]
        return choices(self, population, weights, **kw)
    return pick


def test_random_sequences_saved_for_real_read_back_the_same(tmp_path):
    for seed in range(15):
        st = run_sequence(tmp_path / str(seed), 20_000 + seed, 60)
        Store._save(st)
        st2 = Store(st.path)
        assert parts(st2) == parts(st) and (st2._auto, st2._cut) == (st._auto, st._cut)


# ---------------------------------------------------------------- the routes the pages call
@pytest.fixture
def web(tmp_path, monkeypatch):
    from flask import Flask
    from routes.karaoke import api
    st = Store(tmp_path / "state.json")
    monkeypatch.setattr(api, "store", lambda: st)
    monkeypatch.setattr(api.smconfig, "enabled", lambda cap: True)
    app = Flask(__name__)
    app.register_blueprint(api.karaoke_api, url_prefix="/api")
    c = app.test_client()

    def post(path, **body):
        r = c.post(f"/api/karaoke/{path}", json=body)
        return r.status_code, r.get_json()
    return st, post, c


def test_the_queue_route_adds_moves_and_takes_out(web):
    st, post, _ = web
    a, b, c = song(st, "a"), song(st, "b"), song(st, "c")
    code, r = post("queue", op="add", song_id=a)
    assert code == 200 and r["sung"] == 0 and len(r["queue"]) == 1
    post("queue", op="add", song_id=b, singers=["Ann"])
    post("queue", op="add", song_id=c, next=True)
    assert view(st) == ([], "a", ["c", "b"])
    post("queue", op="add", song_id=c, index=0)          # the page's drop at the very top
    assert view(st) == ([], "a", ["c", "c", "b"])
    code, r = post("queue", op="move", qid=qid(st, "b"), index=0)
    assert code == 200 and view(st) == ([], "a", ["b", "c", "c"])
    assert r["queue"][1]["singers"] == ["Ann"]
    post("queue", op="singers", qid=qid(st, "b"), singers=[])
    assert "singers" not in st.snapshot()["queue"][1]
    post("queue", op="remove", qid=qid(st, "c", 0))
    assert view(st) == ([], "a", ["b", "c"])
    code, r = post("queue", op="clear")
    assert code == 200 and view(st) == ([], "a", []) and r["sung"] == 0


def test_the_queue_route_refuses_what_makes_no_sense_and_changes_nothing(web):
    st, post, _ = web
    post("queue", op="add", song_id=song(st, "a"))
    post("queue", op="add", song_id=song(st, "b"))
    before = (view(st), st.s["player"]["rev"])
    assert post("queue", op="add", song_id="nope")[0] == 404
    assert post("queue", op="add")[0] == 404
    assert post("queue", op="add", song_id=song(st, "x"), index="top")[0] == 400
    assert post("queue", op="move", qid="nope", index=0)[0] == 404
    assert post("queue", op="move", qid=qid(st, "b"))[0] == 400            # no index
    assert post("queue", op="singers", qid="nope", singers=["Ann"])[0] == 404
    assert post("queue", op="explode")[0] == 400
    assert post("queue")[0] == 400
    assert post("queue", op="remove", qid="nope")[0] == 200                # already gone: fine
    assert (view(st), st.s["player"]["rev"]) == before


def test_the_player_route(web):
    st, post, c = web
    ids = {n: song(st, n, ready=n != "d") for n in "abcd"}
    for n in "abcd":
        post("queue", op="add", song_id=ids[n])
    assert view(st) == ([], "a", ["b", "c", "d"])
    code, r = post("player", action="play", qid=qid(st, "c"))
    assert code == 200 and r["player"]["state"] == "playing" and view(st) == (["a"], "c", ["b", "d"])
    assert post("player", action="pause")[1]["player"]["state"] == "paused"
    assert post("player", action="seek", pos_s=20)[1]["player"]["pos_s"] == 20
    assert post("player", action="play")[1]["player"]["state"] == "playing"
    post("player", action="next")
    assert view(st) == (["a", "c"], "b", ["d"])
    post("player", action="prev")
    assert view(st) == (["a"], "c", ["b", "d"])
    code, r = post("player", action="play", qid=qid(st, "d"))             # still being prepared: it waits
    assert code == 200 and view(st) == (["a"], "c", ["d", "b"])
    post("player", action="stop")
    assert view(st) == (["a", "c"], None, ["d", "b"])
    post("player", action="play", song_id=ids["a"])                       # a library song, now
    assert view(st) == (["a", "c"], "a", ["d", "b"])
    s = c.get("/api/karaoke/state").get_json()
    assert s["sung"] == 2 and s["player"]["qid"] == s["queue"][2]["qid"]


def test_the_player_route_says_why_not(web):
    st, post, _ = web
    code, r = post("player", action="play")
    assert code == 409 and "ready" in r["error"]
    x = song(st, "x", ready=False)
    post("queue", op="add", song_id=x)
    post("player", action="stop")
    code, r = post("player", action="play")
    assert code == 409 and "isn't ready yet" in r["error"]
    assert post("player", action="play", qid="nope")[0] == 404
    assert post("player", action="play", song_id="nope")[0] == 404
    assert post("player", action="explode")[0] == 400
    assert post("player")[0] == 400
    failed(st, x)
    assert post("player", action="play", song_id=x)[0] == 409
    assert view(st) == ([], None, ["x"])


def test_a_request_from_the_search_goes_where_it_was_sent(web):
    st, post, _ = web
    for n in "ab":
        post("queue", op="add", song_id=song(st, n))
    code, r = post("requests", title="New One", artist="Them", queue="end", singers=["Ann"])
    assert code == 200 and not r["existing"]
    assert view(st) == ([], "a", ["b", "New One"]) and st.snapshot()["queue"][2]["singers"] == ["Ann"]
    post("requests", title="Next One", artist="Them", queue="next")
    assert view(st) == ([], "a", ["Next One", "b", "New One"])
    post("requests", title="Library Only", artist="Them")
    assert view(st)[2] == ["Next One", "b", "New One"]
    code, r = post("requests", title="Now One", artist="Them", queue="now")       # not prepared yet: it waits at the top
    assert code == 200 and view(st) == ([], "a", ["Now One", "Next One", "b", "New One"])
    ready(st, r["song_id"])
    assert view(st) == (["a"], "Now One", ["Next One", "b", "New One"])
    code, r2 = post("requests", title="Now One", artist="Them", queue="now")      # the library has it: on at once, again
    assert r2["existing"] and r2["song_id"] == r["song_id"]
    assert view(st) == (["a", "Now One"], "Now One", ["Next One", "b", "New One"])
    assert post("requests")[0] == 400
