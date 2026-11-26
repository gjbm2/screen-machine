"""Karaoke's own logic (routes/karaoke; docs/KARAOKE.md): LRC parsing, the display's lines, lining lyrics up with the
vocal, picking the YouTube recording, and the queue/player state machine. No network, no GPU."""
import numpy as np
import pytest

from routes.karaoke import lyrics as lyr
from routes.karaoke import sources as src
from routes.karaoke.store import Store


def test_parse_lrc_sorts_keeps_gaps_and_applies_offset():
    lrc = "[ar:X]\n[offset:+500]\n[00:12.50]Second\n[00:10.00][00:20.00]Twice\n[00:15.00]\n"
    got = lyr.parse_lrc(lrc)
    assert [(e["t"], e["text"]) for e in got] == [(9.5, "Twice"), (12.0, "Second"), (14.5, ""), (19.5, "Twice")]


def test_display_lines_end_at_next_stamp_or_cap():
    entries = [{"t": 1.0, "text": "a b"}, {"t": 3.0, "text": ""}, {"t": 30.0, "text": "one two three four five"}]
    lines = lyr.display_lines(entries, duration=40)
    assert lines[0] == {"start": 1.0, "end": 3.0, "text": "a b"}
    assert lines[1]["start"] == 30.0 and lines[1]["end"] == pytest.approx(30 + 2.0 + 0.6 * 5)


def test_estimate_offset_finds_a_late_vocal():
    starts = [5.0, 9.0, 13.0, 17.0, 21.0, 25.0]
    active = np.zeros(int(35 / lyr.FRAME_S), dtype=bool)
    for s in starts:                                   # the recording sings 1.5 s later than the LRC says
        a = int((s + 1.5) / lyr.FRAME_S)
        active[a: a + int(2.5 / lyr.FRAME_S)] = True
    shift, conf = lyr.estimate_offset(active, [{"start": s} for s in starts])
    assert shift == pytest.approx(1.5, abs=0.06) and conf > 0


def test_estimate_offset_leaves_aligned_lyrics_alone():
    starts = [5.0, 9.0, 13.0, 17.0, 21.0]
    active = np.zeros(int(30 / lyr.FRAME_S), dtype=bool)
    for s in starts:
        a = int(s / lyr.FRAME_S)
        active[a: a + int(2.5 / lyr.FRAME_S)] = True
    assert lyr.estimate_offset(active, [{"start": s} for s in starts])[0] == 0.0


def test_split_video_title_and_scoring():
    assert src.split_video_title("ABBA - Dancing Queen (Official Music Video)", "ABBA") == ("ABBA", "Dancing Queen")
    assert src.split_video_title("Dancing Queen", "ABBA - Topic") == ("ABBA", "Dancing Queen")
    good = {"title": "ABBA - Dancing Queen (Official Audio)", "channel": "ABBA", "duration": 231}
    karaoke = {"title": "Dancing Queen Karaoke Version", "channel": "Sing King", "duration": 231}
    far = {"title": "ABBA - Dancing Queen", "channel": "ABBA", "duration": 290}
    s = lambda v: src.score_video(v, "ABBA", "Dancing Queen", 232)  # noqa: E731
    assert s(good) > s(far) and s(good) > s(karaoke)
    assert src.classify_url("https://open.spotify.com/track/0GjEhVFGZW8afUYGChu3Rr") == "spotify"
    assert src.classify_url("https://youtu.be/xFrGuyw1V8s") == "youtube"


def ready(st, title, secs):
    s = st.add_song({"kind": "text", "q": title})
    st.update_song(s["id"], status="ready", audio_s=secs)
    return s["id"]


def test_the_queue_plays_through_like_a_hifi(tmp_path):
    st = Store(tmp_path / "state.json")
    a, b, c = ready(st, "a", 100.0), ready(st, "b", 50.0), ready(st, "c", 60.0)
    ea, eb, ec = st.queue_add(a), st.queue_add(b), st.queue_add(c)
    p = st.play()                                        # from the top
    assert p["qid"] == ea["qid"] and p["state"] == "playing"
    assert len(st.s["queue"]) == 3                       # entries stay in the queue as they play
    assert st.position(p["at"] + 30) == pytest.approx(30)
    st.tick(p["at"] + 99)
    assert st.s["player"]["qid"] == ea["qid"]            # not over yet
    st.tick(p["at"] + 100.1)                             # over: on to the next entry, from its start
    p2 = st.s["player"]
    assert p2["qid"] == eb["qid"] and p2["pos_s"] == 0
    st.control("next")
    assert st.s["player"]["qid"] == ec["qid"]
    st.control("prev")                                   # at the start of c: back to b
    assert st.s["player"]["qid"] == eb["qid"]
    st.s["player"]["at"] -= 10                           # 10 s into b: prev restarts b first
    st.control("prev")
    assert st.s["player"]["qid"] == eb["qid"] and st.s["player"]["pos_s"] == 0
    st.control("next"); st.tick(st.s["player"]["at"] + 61)   # c ends, nothing after it: stop
    assert st.s["player"]["state"] == "stopped"
    st2 = Store(tmp_path / "state.json")                 # a restart keeps the library and the queue
    assert set(st2.s["songs"]) == {a, b, c} and len(st2.s["queue"]) == 3


def test_not_ready_entries_keep_their_place_and_are_skipped(tmp_path):
    st = Store(tmp_path / "state.json")
    a = ready(st, "a", 30.0)
    slow = st.add_song({"kind": "text", "q": "slow"})["id"]
    b = ready(st, "b", 30.0)
    ea, es, eb = st.queue_add(a), st.queue_add(slow), st.queue_add(b)
    st.play()
    st.control("next")
    assert st.s["player"]["qid"] == eb["qid"]            # skipped the one still being prepared
    with pytest.raises(LookupError):
        st.play(qid=es["qid"])


def test_pause_resume_seek_keep_the_position(tmp_path):
    st = Store(tmp_path / "state.json")
    a = ready(st, "a", 200.0)
    st.queue_add(a)
    st.play()
    st.s["player"]["at"] -= 42                           # 42 s in
    p = st.control("pause")
    assert p["state"] == "paused" and p["pos_s"] == pytest.approx(42, abs=0.1)
    assert st.position(p["at"] + 1000) == pytest.approx(42, abs=0.1)   # paused: the clock stands still
    assert st.control("resume")["pos_s"] == pytest.approx(42, abs=0.1)
    assert st.control("seek", 150)["pos_s"] == 150
    assert st.play()["state"] == "playing"


def test_queue_entries_repeat_move_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(Store, "_start_if_ready", lambda self: None)   # about order: no auto-start on add
    st = Store(tmp_path / "state.json")
    a, b = ready(st, "a", 10.0), ready(st, "b", 10.0)
    e1, e2, e3 = st.queue_add(a), st.queue_add(b), st.queue_add(a)          # a twice
    e0 = st.queue_add(b, index=0)                                            # dropped at the top
    assert [e["qid"] for e in st.s["queue"]] == [e0["qid"], e1["qid"], e2["qid"], e3["qid"]]
    st.queue_move(e3["qid"], 1)
    assert [e["qid"] for e in st.s["queue"]] == [e0["qid"], e3["qid"], e1["qid"], e2["qid"]]
    st.play(qid=e3["qid"])                               # nothing playing: it moves to the top and plays
    assert [e["qid"] for e in st.s["queue"]] == [e3["qid"], e0["qid"], e1["qid"], e2["qid"]]
    st.queue_remove(e3["qid"])                           # taking out the one playing: on to the next
    assert st.s["player"]["qid"] == e0["qid"] and len(st.s["queue"]) == 3
    p = st.play(sid=b)                                   # a library song now: a new entry right after the current
    assert [e["song_id"] for e in st.s["queue"]][1] == b and p["qid"] == st.s["queue"][1]["qid"]
    st.remove_song(b)                                    # deleting from the library takes its entries too
    assert [e["song_id"] for e in st.s["queue"]] == [a] and st.s["player"]["state"] == "stopped"


def test_restart_requeues_unfinished_work(tmp_path):
    st = Store(tmp_path / "state.json")
    a = st.add_song({"kind": "text", "q": "a"})
    st.update_song(a["id"], status="separating")
    assert Store(tmp_path / "state.json").s["songs"][a["id"]]["status"] == "queued"


# ---------------------------------------------------------------- fitting lyrics to what is sung (25 Nov: Happy)
def _sung(text, t=1.0, gap=0.4):
    out = []
    for w in text.split():
        out.append({"w": w, "t0": round(t, 2), "t1": round(t + 0.3, 2)})
        t += gap
    return out


def test_fit_times_lines_to_the_singing_and_matches_choruses_in_order():
    from routes.karaoke import lyrics as L
    sung = _sung("it might seem crazy", 2.0) + _sung("clap along if you feel", 10.0) + _sung("clap along if you feel", 20.0)
    lines, st = L.fit_to_sung(["It might seem crazy", "Clap along, if you feel", "(Clap along if you feel)"], sung, 30)
    assert [ln["start"] for ln in lines] == [2.0, 10.0, 20.0]
    assert st["coverage"] == 1.0
    assert lines[0]["end"] <= lines[1]["start"]


def test_fit_places_unheard_lines_between_neighbours():
    from routes.karaoke import lyrics as L
    sung = _sung("one two three", 0.0) + _sung("seven eight nine", 10.0)
    lines, _ = L.fit_to_sung(["one two three", "four five six", "seven eight nine"], sung, 20)
    assert lines[1]["start"] == 5.0


def test_best_fit_prefers_the_upload_that_matches_the_singing():
    from routes.karaoke import lyrics as L
    sung = _sung("it might seem crazy what I'm about to say sunshine she's here you can take a break")
    wrong = {"kind": "synced", "lrc": "[00:12.00]Let me tell you now\n[00:16.00]Here we go\n[00:17.00]It might seem crazy"}
    right = {"kind": "plain", "text": "It might seem crazy what I'm about to say\nSunshine she's here, you can take a break"}
    best, lines, st = L.best_fit([wrong, right], sung)
    assert best is right and lines[0]["start"] == 1.0 and st["coverage"] > 0.9
    none, _, _ = L.best_fit([wrong], _sung("totally different words sung here by someone else entirely ok"))
    assert none is None


def test_lines_from_sung_breaks_at_pauses():
    from routes.karaoke import lyrics as L
    lines = L.lines_from_sung(_sung("a b c", 0.0) + _sung("d e", 5.0))
    assert [ln["text"] for ln in lines] == ["a b c", "d e"] and lines[1]["start"] == 5.0


def test_up_next_goes_right_after_the_entry_the_player_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(Store, "_start_if_ready", lambda self: None)   # about order: no auto-start on add
    st = Store(tmp_path / "state.json")
    a, b, c, d = (ready(st, x, 60.0) for x in "abcd")
    st.queue_add(a); st.queue_add(b)
    e = st.queue_add(c, next=True)                       # nothing playing: the top
    assert [x["qid"] for x in st.s["queue"]][0] == e["qid"]
    st.play(qid=st.s["queue"][1]["qid"])                 # playing a (second): it moves to the top
    e2 = st.queue_add(d, next=True)
    assert [x["song_id"] for x in st.s["queue"]] == [a, d, c, b] and st.s["queue"][1]["qid"] == e2["qid"]
    st.control("stop")                                   # stopped on a: Play carries on after it, so next is b
    st.queue_add(b, next=True)
    assert [x["song_id"] for x in st.s["queue"]] == [a, b, d, c, b]
    assert st.play()["qid"] == st.s["queue"][1]["qid"]


def test_the_song_search_merges_catalogues_and_youtube_puts_the_meant_song_first(monkeypatch):
    def itunes(q, limit):
        if q == "Happy Pharrell Williams":                # the corrected lookup
            return [{"artist": "Pharrell Williams", "title": 'Happy (From "Despicable Me 2")', "album": "G I R L",
                     "duration": 233, "art": None, "from": "itunes"}]
        return [{"artist": "Noteservice Wind Ensemble", "title": "Happy (Pharrell Williams)", "album": None,
                 "duration": 123, "art": None, "from": "itunes"},
                {"artist": "Pharrell Williams", "title": "Happy (Live)", "album": None, "duration": 201, "art": None,
                 "from": "itunes"},
                {"artist": "Will Ferrell", "title": "Happy", "album": None, "duration": 86, "art": None, "from": "itunes"}]

    def deezer(q, limit):
        return [{"artist": "La Banda", "title": "Hay Amores", "album": None, "duration": 277, "art": None, "from": "deezer"},
                {"artist": "Will Ferrell", "title": "Happy - Single Version", "album": None, "duration": 86, "art": None,
                 "from": "deezer"}]
    monkeypatch.setattr(src, "_itunes", itunes)
    monkeypatch.setattr(src, "_deezer", deezer)
    monkeypatch.setattr(src, "_youtube_guesses", lambda q, n=4: [("Pharrell Williams", "Happy")])
    rows = src.catalog_search("hapy farell")
    assert (rows[0]["title"], rows[0]["artist"]) == ("Happy", "Pharrell Williams")      # the edition words go
    assert not any("Live" in r["title"] or "Ensemble" in r["artist"] for r in rows)    # versions not asked for
    assert [r["key"] for r in rows].count("will ferrell|happy") == 1                   # one row per song

    monkeypatch.setattr(src, "_youtube_guesses", lambda q, n=4: (_ for _ in ()).throw(OSError("no yt")))
    assert src.catalog_search("hapy farell")[0]["artist"] in ("Will Ferrell", "La Banda")   # catalogues alone

    def down(q, limit):
        raise OSError("down")
    monkeypatch.setattr(src, "_itunes", down)
    monkeypatch.setattr(src, "_deezer", down)
    monkeypatch.setattr(src, "lrclib_search", lambda q, limit=8: [{"artist": "A", "title": "T", "duration": 1,
                                                                    "lrclib_id": 1, "synced": True}])
    assert src.catalog_search("x")[0]["from"] == "lrclib"                                # both down: LRCLIB


def test_a_song_already_in_the_library_is_found_whatever_the_artists_word_order(tmp_path):
    from routes.karaoke.store import song_key
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "Williams, Pharrell", "title": "Happy"})
    st.update_song(s["id"], artist="Williams, Pharrell", title="Happy")
    assert st.find_existing(key=song_key("Pharrell Williams", "Happy"))["id"] == s["id"]
    assert st.find_existing(key=song_key("Pharrell", "Happy")) is None


def _room(delay_s, clicks, sr=48000, noise=0.01, gain=0.2, seed=1):
    from routes.karaoke import calibrate as cal
    rng = np.random.default_rng(seed)
    t0 = clicks[0] - 1.0
    n = int((clicks[-1] + 3.5 - t0) * sr)
    sig = noise * rng.standard_normal(n)
    sig += 0.05 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)          # someone humming along
    for ck, sp in zip(clicks, cal.chirp_specs(len(clicks))):
        c = cal.chirp(sr, sp["f0"], sp["f1"], sp["dur_s"])
        k = np.convolve(c, [1.0, 0, 0, 0.4, 0, -0.2])[: len(c) + 5]      # the speaker and the room smear it
        i = int(round((ck + delay_s - t0) * sr))
        sig[i:i + len(k)] += gain * k
    return t0, sig


def test_the_calibration_finds_the_speaker_delay_through_overlapping_levels_frames():
    from routes.karaoke import calibrate as cal
    sr = 48000
    clicks = [1000.0 + o for o in cal.OFFSETS_S]
    t0, sig = _room(0.231, clicks)
    frames, i = [], 2048                                                # windows of the newest 2048, ~60 a second
    while i < len(sig):
        if not (1.7 * sr < i < 1.8 * sr):                              # a lost stretch
            frames.append((t0 + (i - 1) / sr, sig[i - 2048:i].copy()))
        i += 800
    a0, s = cal.assemble(frames, sr)
    r = cal.measure(a0, s, sr, clicks)
    assert r["ok"] and abs(r["delay_ms"] - 231) <= 2 and r["agree"] == r["used"] == 7, r


def test_the_calibration_refuses_a_room_that_drowned_the_chirps():
    from routes.karaoke import calibrate as cal
    clicks = [50.0 + o for o in cal.OFFSETS_S]
    t0, sig = _room(0.2, clicks, noise=0.5, gain=0.02)
    r = cal.measure(t0, sig, 48000, clicks)
    assert not r["ok"] and r["why"]
    t0, sig = _room(0.2, clicks, gain=0.0)
    assert not cal.measure(t0, sig, 48000, clicks)["ok"]


def test_songs_carry_the_searchs_facts_and_are_tagged_by_genre_and_decade(tmp_path):
    from routes.karaoke.store import public_song
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "ABBA", "title": "Dancing Queen", "genre": "Pop", "year": 1976,
                     "rank": 908295})
    ps = public_song(st.s["songs"][s["id"]])
    assert (ps["genre"], ps["year"], ps["rank"], ps["tags"]) == ("Pop", 1976, 908295, ["Pop", "1970s"])
    assert public_song(st.add_song({"kind": "text", "q": "x"}))["tags"] == []


def test_an_artists_songs_are_the_best_known_artists_most_popular_first(monkeypatch):
    calls = []

    def get(url, params):
        calls.append(url)
        if url.endswith("/artist"):
            return {"data": [{"id": 1, "name": "Tayler Swift", "nb_fan": 0}, {"id": 2, "name": "Taylor Swift", "nb_fan": 9}]}
        assert url.endswith("/artist/2/top")
        row = lambda t, r: {"title": t, "rank": r, "duration": 200, "artist": {"name": "Taylor Swift"}, "album": {"title": "A"}}
        return {"data": [row("Shake It Off", 9), row("Shake It Off (Taylor's Version)", 8), row("Love Story - Karaoke", 7),
                         row("Love Story", 6)]}
    monkeypatch.setattr(src, "_get_json_quick", get)
    o = src.artist_top("tayler swift")
    assert o["artist"] == "Taylor Swift" and [r["title"] for r in o["results"]] == ["Shake It Off", "Love Story"]   # one row a song


def test_the_calibration_listens_on_show_services_websocket(tmp_path):
    from routes.karaoke.calibrate import Calibrator
    assert Calibrator(Store(tmp_path / "s.json"), "http://127.0.0.1:8703").levels_url == "ws://127.0.0.1:8703/v1/levels?wave=2048"


def test_each_chirp_reads_the_delay_on_its_own_so_long_delays_and_lost_chirps_are_fine():
    from routes.karaoke import calibrate as cal
    clicks = [500.0 + o for o in cal.OFFSETS_S]
    t0, sig = _room(2.3, clicks)                                         # longer than any chirp gap
    lost = int(round((clicks[4] + 2.3 - t0) * 48000))
    sig[lost:lost + 4000] = 0                                            # one chirp lost
    r = cal.measure(t0, sig, 48000, clicks)
    assert r["ok"] and abs(r["delay_ms"] - 2300) <= 2 and r["agree"] >= 6, r   # the lost one outvoted


def test_the_first_chirps_while_the_speaker_wakes_are_left_out():
    from routes.karaoke import calibrate as cal
    sr = 48000
    clicks = [700.0 + o for o in cal.OFFSETS_S]
    t0, sig = _room(0.41, clicks, gain=0.0)
    for k, (ck, sp) in enumerate(zip(clicks, cal.chirp_specs(len(clicks)))):
        d = 0.41 + (0.6 if k < 2 else 0.0)                                 # the first two late: still waking
        c = cal.chirp(sr, sp["f0"], sp["f1"], sp["dur_s"])
        i = int(round((ck + d - t0) * sr))
        sig[i:i + len(c)] += 0.2 * c
    r = cal.measure(t0, sig, sr, clicks)
    assert r["ok"] and abs(r["delay_ms"] - 410) <= 2 and r["readings_ms"][:2] == [1010, 1010], r


def test_playing_a_later_entry_moves_it_up_and_keeps_the_others(tmp_path):
    st = Store(tmp_path / "state.json")
    a, b, c, d = (ready(st, x, 60.0) for x in "abcd")
    ea, eb, ec, ed = (st.queue_add(x) for x in (a, b, c, d))
    st.play(qid=ea["qid"])
    st.play(qid=ed["qid"])                               # d jumps ahead of b and c; a is now sung
    assert [e["song_id"] for e in st.s["queue"]] == [a, d, b, c] and st.s["player"]["qid"] == ed["qid"]
    st.control("next")
    assert st.s["player"]["qid"] == eb["qid"]            # then on as before: b, c
    st.play(qid=ea["qid"])                               # an already-sung one: again, after the current
    assert [e["song_id"] for e in st.s["queue"]] == [d, b, a, c] and st.s["player"]["qid"] == ea["qid"]


def test_a_song_that_sings_at_once_gets_a_silent_lead_in(tmp_path):
    from routes.karaoke.store import LEAD_IN_S, public_song
    st = Store(tmp_path / "state.json")
    a, b = ready(st, "a", 100.0), ready(st, "b", 100.0)
    st.update_song(a, lyrics={"kind": "synced", "lines": [{"start": 1.5, "end": 4, "text": "x"}]})
    st.update_song(b, lyrics={"kind": "synced", "lines": [{"start": 20.0, "end": 24, "text": "y"}]})
    ea, eb = st.queue_add(a), st.queue_add(b)
    p = st.play()
    assert p["pos_s"] == -(LEAD_IN_S - 1.5)                   # title up 8 s before the first line
    assert st.position(p["at"] + 1.0) == pytest.approx(-(LEAD_IN_S - 1.5) + 1.0)
    st.tick(p["at"] + 100 + (LEAD_IN_S - 1.5) - 0.1)
    assert st.s["player"]["qid"] == ea["qid"]                 # the lead-in doesn't eat into the song
    st.tick(p["at"] + 100 + (LEAD_IN_S - 1.5) + 0.1)
    assert st.s["player"]["qid"] == eb["qid"] and st.s["player"]["pos_s"] == 0   # b has a long intro: none
    st.control("restart")
    assert st.s["player"]["pos_s"] == 0
    assert public_song(st.s["songs"][a])["lead_in_s"] == LEAD_IN_S - 1.5


def test_child_mode_spots_obvious_profanity_only():
    from routes.karaoke import profanity as P
    for w in ("fucking", "Fuckin'", "motherfucker", "shit,", "bullshit", "bitch", "goddamn", "cunt", "nigga", "bastards"):
        assert P.is_profane(w), w
    for w in ("class", "assess", "damn", "hell", "crap", "dickens", "titanic", "pussycat", "Scunthorpe", "special"):
        assert not P.is_profane(w), w
    assert P.mask_text("Fuck You") == "---- You"


def test_child_mode_serves_lyrics_with_dashes_and_the_times_to_mute(tmp_path):
    from routes.karaoke import profanity as P
    doc = {"kind": "synced", "lines": [
        {"start": 1, "end": 3, "text": "You're so fucking special",
         "words": [{"t0": 1, "t1": 1.2}, {"t0": 1.2, "t1": 1.4}, {"t0": 1.4, "t1": 1.8}, {"t0": 1.8, "t1": 2.5}]},
        {"start": 5, "end": 7, "text": "No words timed, shit", "words": None}]}
    out = P.censor(doc, [{"w": "shit", "t0": 6.0, "t1": 6.3}, {"w": "special", "t0": 1.8, "t1": 2.5}])
    assert [l["text"] for l in out["lines"]] == ["You're so ------- special", "No words timed, ----"]
    assert out["lines"][0]["bad"] == [2] and out["lines"][1]["bad"] == [3]
    pad = P.MUTE_PAD_S
    assert out["mutes"] == [[round(1.4 - pad, 3), round(1.8 + pad, 3)], [round(6.0 - pad, 3), round(6.3 + pad, 3)]]
    assert doc["lines"][0]["text"] == "You're so fucking special"      # the stored lyrics are untouched
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "CeeLo Green", "title": "Fuck You"})
    st.queue_add(s["id"])
    st.s["player"].update({"state": "paused", "song_id": s["id"]})
    assert st.now_playing()["song"]["title"] == "Fuck You"
    st.settings(child_mode=True)
    assert st.now_playing()["song"]["title"] == "---- You"


def test_child_mode_lyrics_route_reads_sung_json_as_a_list(tmp_path, monkeypatch):
    """25 Nov: sung.json is a list; the route assumed an object and returned 500, so the screen said "No lyrics"."""
    import json as _json
    from flask import Flask
    from routes.karaoke import api
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "Radiohead", "title": "Creep"})
    st.update_song(s["id"], status="ready", lyrics={"kind": "synced", "lines": [
        {"start": 1, "end": 3, "text": "You're so fucking special",
         "words": [{"t0": 1, "t1": 1.2}, {"t0": 1.2, "t1": 1.4}, {"t0": 1.4, "t1": 1.8}, {"t0": 1.8, "t1": 2.5}]}]})
    d = tmp_path / s["id"]
    d.mkdir()
    (d / "sung.json").write_text(_json.dumps([{"w": "fucking", "t0": 1.4, "t1": 1.8}]))
    monkeypatch.setattr(api, "store", lambda: st)
    monkeypatch.setattr(api, "song_dir", lambda sid: tmp_path / sid)
    monkeypatch.setattr(api.smconfig, "enabled", lambda cap: True)   # the karaoke capability, whatever the deployment
    app = Flask(__name__)
    app.register_blueprint(api.karaoke_api, url_prefix="/api")
    st.settings(child_mode=True)
    r = app.test_client().get(f"/api/karaoke/songs/{s['id']}/lyrics")
    assert r.status_code == 200 and r.get_json()["lines"][0]["text"] == "You're so ------- special"
    (d / "sung.json").write_text("{not json")
    assert app.test_client().get(f"/api/karaoke/songs/{s['id']}/lyrics").status_code == 200


def test_a_song_added_to_an_idle_player_plays_when_ready(tmp_path):
    st = Store(tmp_path / "state.json")
    a = ready(st, "a", 60.0)
    st.queue_add(a)
    assert st.s["player"]["state"] == "playing"                 # nothing playing, nothing waiting: it plays
    st.control("stop")
    b = ready(st, "b", 60.0)
    c = st.add_song({"kind": "text", "q": "c"})                  # still being prepared
    st.queue_add(b)
    assert st.s["player"]["state"] == "playing" and st.s["player"]["song_id"] == b   # stopped at the end: plays
    st.control("stop")
    st.queue_add(c["id"])
    st.tick()
    assert st.s["player"]["state"] == "stopped"                 # not ready yet
    st.update_song(c["id"], status="ready", audio_s=60.0)
    st.tick()
    assert st.s["player"]["state"] == "playing" and st.s["player"]["song_id"] == c["id"]   # ready: it starts


def test_adding_after_a_stop_mid_queue_does_not_start_and_clear_keeps_the_song_playing(tmp_path):
    st = Store(tmp_path / "state.json")
    a, b, c = (ready(st, x, 60.0) for x in "abc")
    st.queue_add(a); st.queue_add(b)                             # a starts at once (idle add)
    st.control("stop")                                           # stopped on a, b still waiting
    st.queue_add(c)
    assert st.s["player"]["state"] == "stopped"                  # songs were waiting: Play is the person's call
    st.play()
    playing = st.s["player"]["qid"]
    st.queue_clear()
    assert [e["qid"] for e in st.s["queue"]] == [playing] and st.s["player"]["state"] == "playing"
    st.control("stop")
    st.queue_clear()
    assert st.s["queue"] == [] and st.s["player"]["state"] == "stopped"


def test_the_karaoke_screen_and_sound_are_settings(tmp_path):
    st = Store(tmp_path / "state.json")
    assert st.s["settings"]["screen"] is None and st.s["settings"]["output"] == "screen"
    st.settings(screen="north", output="none")
    assert (st.s["settings"]["screen"], st.s["settings"]["output"]) == ("north", "none")
    st.settings(screen=None)
    assert st.s["settings"]["screen"] is None
    st.settings(output="sonos:living")                       # a Sonos room (the api checks it is the deployment's)
    assert st.s["settings"]["output"] == "sonos:living"
    for bad in ({"screen": "../x"}, {"output": "sonos:"}, {"output": "loud"}):
        with pytest.raises(ValueError):
            st.settings(**bad)


class _FakeSonos:
    def __init__(self, zone, emit):
        self.zone, self.emit, self.starts, self.stops, self.heard_in = zone, emit, [], 0, 1.0
        self.now = 0.0

    def start(self, path, lead_in_ms=0.0, url=None, title=None):
        self.starts.append({"path": str(path), "lead_ms": lead_in_ms, "url": url, "title": title})
        return self.now + self.heard_in + lead_in_ms / 1000.0

    def stop(self):
        self.stops += 1


def _sonos_rig(tmp_path):
    from routes.karaoke.sonos_out import KaraokeSonos
    st = Store(tmp_path / "state.json")
    sid = ready(st, "a", 100.0)
    st.update_song(sid, lyrics={"kind": "synced", "lines": [{"start": 2.0, "end": 4, "text": "fucking hell",
                                                              "words": [{"t0": 2.0, "t1": 2.4}, {"t0": 2.4, "t1": 2.8}]}]})
    outs, mixes = [], []
    clock = {"mono": 1000.0, "wall": 5000.0}

    def factory(zone, emit):
        o = _FakeSonos(zone, emit)
        o.now = clock["mono"]
        outs.append(o)
        return o

    def mixer(sid_, from_s, guide, mutes):
        mixes.append((sid_, round(from_s, 2), guide, mutes))
        return tmp_path / ("%032x.flac" % len(mixes))
    d = KaraokeSonos(st, output_factory=factory, mixer=mixer, url_for=lambda k: f"http://lan:8700/show/x/{k}.flac",
                     mono=lambda: clock["mono"], wall=lambda: clock["wall"])
    return st, sid, d, outs, mixes, clock


def test_karaoke_on_a_sonos_room_plays_the_mix_and_follows_what_the_room_hears(tmp_path):
    from routes.karaoke.store import LEAD_IN_S
    st, sid, d, outs, mixes, clock = _sonos_rig(tmp_path)
    st.settings(output="sonos:living", guide=0.25)
    d.step()
    assert outs == []                                            # nothing playing: nothing on the Sonos
    st.queue_add(sid)                                            # idle add: it plays (with a lead-in: first line at 2 s)
    d.step()
    lead = LEAD_IN_S - 2.0
    o = outs[0]
    assert o.zone == "living" and mixes[0][:3] == (sid, 0.0, 0.25)
    assert abs(o.starts[0]["lead_ms"] - lead * 1000) < 60 and o.starts[0]["title"] == "Karaoke: a"
    assert st.position(clock["wall"]) == pytest.approx(-(1.0 + lead), abs=0.06)   # heard 1 s + the lead-in from now
    o.emit({"ev": "rebase", "t_audio0": clock["mono"] + 1.0 + lead + 0.2})          # the Sonos's own position: later
    assert st.position(clock["wall"]) == pytest.approx(-(1.2 + lead), abs=0.06)
    d.step()
    assert len(o.starts) == 1                                    # nothing changed: not played again
    st.settings(guide=1.0)                                       # the guide level: a new mix from where it is
    d.step()
    assert len(o.starts) == 2 and mixes[-1][2] == 1.0
    st.settings(child_mode=True)
    d.step()
    assert mixes[-1][3] and mixes[-1][3][0][0] < 2.0 < mixes[-1][3][0][1]   # child mode mutes the swear word
    assert o.starts[-1]["title"] == "Karaoke: a"
    st.control("pause")
    d.step()
    assert o.stops == 1
    st.settings(output="screen")
    st.control("resume")
    d.step()
    assert len(o.starts) == 3                                    # the screen plays it now, not the Sonos


def test_make_mix_mixes_the_vocal_at_the_guide_level_with_mutes(tmp_path, monkeypatch):
    import subprocess, soundfile as sf
    from routes.karaoke import sonos_out
    d = tmp_path / "song"
    d.mkdir()
    for name, f in (("instrumental", 220), ("vocals", 880)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={f}:duration=4",
                        "-c:a", "aac", str(d / f"{name}.m4a")], check=True)
    monkeypatch.setattr(sonos_out, "song_dir", lambda sid: d)
    out = sonos_out.make_mix("k1", 1.0, 1.0, [[2.0, 2.5]], folder=tmp_path / "cache")
    assert out.suffix == ".flac" and len(out.stem) == 32
    a, sr = sf.read(str(out))
    assert sr == 48000 and abs(len(a) / sr - 3.0) < 0.15        # from 1 s of a 4 s song
    seg = lambda t0, t1: a[int(t0 * sr):int(t1 * sr), 0]
    import numpy as np
    loud_mix, muted = np.abs(seg(0.2, 0.8)).max(), np.abs(seg(1.1, 1.4)).max()   # 2.0-2.5 of the song: 1.0-1.5 here
    assert muted < loud_mix * 0.8                                # the vocal is out over the muted word
    assert sonos_out.make_mix("k1", 1.0, 1.0, [[2.0, 2.5]], folder=tmp_path / "cache") == out   # made once


def test_a_song_cut_short_carries_on_and_a_stuck_one_restarts_from_nothing(tmp_path, monkeypatch):
    from routes.karaoke import worker as W
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "David Bowie", "title": "Heroes"})
    d = tmp_path / s["id"]
    monkeypatch.setattr(W, "song_dir", lambda sid: tmp_path / sid)
    monkeypatch.setattr(W, "info", lambda *a, **k: None)          # not into the app's own log
    calls = []
    w = W.Worker(st)
    monkeypatch.setattr(w, "resolve", lambda req: calls.append("resolve") or
                        {"url": "u", "video_id": "v", "artist": "David Bowie", "title": "Heroes", "duration": 360})
    def download(url, dd):
        calls.append("download")
        (dd / "source.m4a").write_bytes(b"x")
        return dd / "source.m4a"
    monkeypatch.setattr(W.src, "download_audio", download)
    monkeypatch.setattr(W.src, "lrclib_lyrics", lambda *a: None)
    monkeypatch.setattr(W.src, "lrclib_candidates", lambda *a: [])

    def separate(audio, dd):
        calls.append("separate")
        raise W.SourceError("cut short")                         # Flask went mid-separation
    monkeypatch.setattr(w, "separate", separate)
    import pathlib
    monkeypatch.setattr(W.subprocess, "run", lambda *a, **k: pathlib.Path(a[0][-1]).write_bytes(b"w"))
    with pytest.raises(W.SourceError):
        w.process({"id": s["id"], "request": s["request"]})
    assert calls == ["resolve", "download", "separate"]
    assert st.s["songs"][s["id"]]["source_url"] == "u" and (d / "source.wav").is_file()
    calls.clear()
    with pytest.raises(W.SourceError):
        w.process({"id": s["id"], "request": s["request"]})     # again: no new lookup, no new download
    assert calls == ["separate"]
    w.restart(s["id"])                                           # Restart: from nothing
    assert not d.exists() and st.s["songs"][s["id"]]["status"] == "queued" and not st.s["songs"][s["id"]]["source_url"]


def test_a_spotify_playlist_is_read_from_its_embed_page_and_imported_skipping_what_the_library_has(tmp_path, monkeypatch):
    import io, json as _json
    from flask import Flask
    from routes.karaoke import api
    page = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Party", "trackList": [
        {"title": "Dancing Queen", "subtitle": "ABBA", "duration": 231000},
        {"title": "Flower In A Hurricane (feat. Tame Impala) [From GTAVI: Original Score]", "subtitle": "Justice, Tame Impala",
         "duration": 224877}]}}}}}}
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{_json.dumps(page)}</script></html>'

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    seen = []
    monkeypatch.setattr(src.urllib.request, "urlopen", lambda req, timeout=None: seen.append(req.full_url) or R(html.encode()))
    o = src.spotify_list("https://open.spotify.com/intl-de/playlist/AbC123?si=x")
    assert seen == ["https://open.spotify.com/embed/playlist/AbC123"]
    assert o["name"] == "Party" and o["tracks"] == [
        {"title": "Dancing Queen", "artist": "ABBA", "duration": 231},
        {"title": "Flower In A Hurricane", "artist": "Justice", "duration": 225}]
    with pytest.raises(src.SourceError):
        src.spotify_list("https://open.spotify.com/track/xyz")
    st = Store(tmp_path / "state.json")
    st.add_song({"kind": "lrclib", "artist": "ABBA", "title": "Dancing Queen"})
    monkeypatch.setattr(api, "store", lambda: st)
    monkeypatch.setattr(api.smconfig, "enabled", lambda cap: True)
    app = Flask(__name__)
    app.register_blueprint(api.karaoke_api, url_prefix="/api")
    r = app.test_client().post("/api/karaoke/import", json={"url": "https://open.spotify.com/playlist/AbC123", "queue": True})
    assert r.get_json() == {"name": "Party", "added": 1, "had": 1, "total": 2}
    assert len(st.s["songs"]) == 2 and len(st.s["queue"]) == 2


def test_whispers_stock_lines_on_silence_are_not_lyrics():
    from routes.karaoke.worker import real_heard
    ty = [{"w": "Thank", "t0": t, "t1": t + .1} for t in range(0, 180, 30)] + \
         [{"w": "you.", "t0": t + .1, "t1": t + .2} for t in range(0, 180, 30)]
    assert real_heard(sorted(ty, key=lambda w: w["t0"]), 187) == []
    sung = [{"w": w, "t0": i * 0.5, "t1": i * 0.5 + 0.4} for i, w in enumerate(("hello darkness my old friend " * 20).split())]
    assert len(real_heard(sung, 187)) == 100


def test_a_silent_lead_stem_is_separated_again_with_every_voice_out(tmp_path, monkeypatch):
    from routes.karaoke import worker as W
    st = Store(tmp_path / "state.json")
    s = st.add_song({"kind": "lrclib", "artist": "Simon & Garfunkel", "title": "The Sound of Silence"})
    d = tmp_path / "d"
    d.mkdir()
    for f in ("vocals.wav", "instrumental.wav", "source.wav", "vocals.m4a", "sung.json"):
        (d / f).write_bytes(b"x")
    levels = {"vocals.wav": -87.0, "instrumental.wav": -18.0}
    monkeypatch.setattr(W, "mean_db", lambda p: levels[p.name])
    monkeypatch.setattr(W, "info", lambda *a, **k: None)
    w = W.Worker(st)
    seps = []
    monkeypatch.setattr(w, "separate", lambda audio, dd, model=None: seps.append(model))
    w.check_lead(s["id"], d, d / "source.wav")
    assert seps == [W.ALL_VOCALS_MODEL] and st.s["songs"][s["id"]]["separation"] == "all"
    assert not (d / "vocals.m4a").exists() and not (d / "sung.json").exists()
    w.check_lead(s["id"], d, d / "source.wav")
    assert len(seps) == 1                                        # once


def test_title_cleanup_keeps_a_title_that_ends_in_a_number():
    assert src._clean_title("9 to 5") == "9 to 5" and src._clean_title("Song 2") == "Song 2"
    assert src._clean_title("Dancing Queen_copy") == "Dancing Queen" and src._clean_title("Dancing Queen_2") == "Dancing Queen"


def test_the_karaoke_screen_goes_live_with_a_song_and_back_to_art_on_stop_only(tmp_path, monkeypatch):
    import time
    from routes.karaoke import live_view
    from routes.karaoke.live_view import LIVE, LiveView
    monkeypatch.setattr(live_view, "info", lambda *a, **k: None)     # not into the app's own log
    st = Store(tmp_path / "state.json")
    a, b = (ready(st, x, 60.0) for x in "ab")
    screen = {"desired": "art"}
    calls = []

    def room(method, path, body=None):
        if method == "GET":
            return {"screens": {"vj": dict(screen)}}
        calls.append(body["desired"])
        screen["desired"] = body["desired"]

    lv = LiveView(st, room=room, screen_of=lambda s: s or "vj", room_mode=None)
    lv.step()                                                     # the first look: nothing
    st.queue_add(a); lv.step()                                    # an idle add starts it: live
    assert calls == [LIVE]
    st.queue_add(b); st.control("next"); lv.step()                # song changes leave it be
    st.control("pause"); lv.step(); st.control("resume"); lv.step()
    assert calls == [LIVE]
    st.tick(time.time() + 3600); lv.step()                        # the queue ran out: still live
    assert st.s["player"]["state"] == "stopped" and calls == [LIVE]
    st.control("restart"); lv.step(); st.control("stop"); lv.step()   # Stop: back to art
    assert calls == [LIVE, "art"]


def test_karaoke_as_a_room_mode_enters_it_with_a_song_and_puts_the_old_mode_back_on_stop(tmp_path, monkeypatch):
    """Owner, 26 Nov: "karaoke should be a mode, right? like disco or ambient" (deployment.yaml karaoke.room_mode)."""
    import time
    from routes.karaoke import live_view
    from routes.karaoke.live_view import LiveView
    monkeypatch.setattr(live_view, "info", lambda *a, **k: None)
    st = Store(tmp_path / "state.json")
    a = ready(st, "a", 60.0)
    mode = {"current": "gallery", "target": None, "default": "gallery"}
    puts = []

    def room(method, path, body=None):
        if method == "GET":
            return {"mode": dict(mode)}
        assert path == "/api/v1/mode"
        puts.append(body["mode"])
        mode["current"] = body["mode"]

    lv = LiveView(st, room=room, screen_of=lambda s: "vj", room_mode="singalong")
    lv.step()
    st.queue_add(a); lv.step()                                    # a song starts: the room goes Karaoke
    assert puts == ["singalong"]
    st.tick(time.time() + 3600); lv.step()                        # the queue ran out: still Karaoke
    assert puts == ["singalong"]
    st.control("stop"); lv.step()                                 # Stop: back to the mode before
    assert puts == ["singalong", "gallery"]
    mode["current"] = "disco"                                     # a person's own mode in between is theirs
    st.queue_add(a); lv.step(); mode["current"] = "disco"; st.control("stop"); lv.step()
    assert puts == ["singalong", "gallery", "singalong"]
