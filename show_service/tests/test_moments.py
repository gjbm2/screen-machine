"""A song's moments: which are chosen (moments.choose), what each puts in the look (moments.perform), and the
envelopes they make (phrasing: stop, out)."""
from show_service import moments as M
from show_service import phrasing

CFG = {"lead_s": 3.0,
       "kinds": {"stab": {"envelope": "bump", "lift": 0.5, "settle_beats": 1.0, "hit_beats": 0.5, "flash": True},
                 "stop": {"envelope": "stop", "floor": 0.03, "lift": 0.4, "settle_beats": 1.0},
                 "drums": {},
                 "end": {"cold": {"envelope": "out", "hold_s": 0.3, "fade_s": 0.15, "floor": 0.0},
                         "fade": {"envelope": "out", "fade": "follow", "floor": 0.0}}},
       "choose": {"min_salience": 0.5, "gap_bars": 8, "default": {"stab": 2, "stop": 1},
                  "by_idiom": {"hush": {"stab": 0, "stop": 1, "floor": 0.3}}}}
SONG = [{"t": 20.0, "kind": "stab", "salience": 0.9}, {"t": 25.0, "kind": "stab", "salience": 0.95},
        {"t": 60.0, "kind": "stab", "salience": 0.7}, {"t": 90.0, "kind": "stab", "salience": 0.4},
        {"t": 100.0, "end": 101.0, "kind": "stop", "salience": 0.8},
        {"t": 120.0, "end": 130.0, "kind": "drums", "salience": 0.9},
        {"t": 200.0, "end": 200.6, "kind": "end", "style": "cold", "salience": 1.0}]


def test_a_song_gets_its_strongest_moments_spaced_apart_and_always_its_ending():
    got = M.choose(SONG, None, 0.5, CFG)
    # stabs: 25 (strongest) then 60 (20 is within 8 bars of 25); 90 is under min_salience; drums have no recipe
    assert [(m["kind"], m["t"]) for m in got] == [("stab", 25.0), ("stab", 60.0), ("stop", 100.0), ("end", 200.0)]
    hush = M.choose(SONG, "hush", 0.5, CFG)
    assert [m["kind"] for m in hush] == ["stop", "end"]                 # a hushed song takes no stabs


def test_a_moment_is_handed_on_ahead_of_its_line_in_host_time():
    got = M.choose(SONG, None, 0.5, CFG)
    # song at 23 s, host at 1000 s: the stab at 25 is 2 s ahead (inside lead_s), the one at 60 isn't yet
    p = M.perform(got, 23.0, 1000.0, 0.5, CFG, None)
    assert [e["kind"] for e in p["envelopes"]] == ["bump"]
    assert abs(p["envelopes"][0]["at_ms"] - 1002000.0) < 1.0 and p["envelopes"][0]["wind"] == 0.0
    assert p["hits"] == [(1002.0, 1002.25)] and p["flashes"] == [("stab:1", 1002.0)]
    # the stop lands on the band's return, dark for the gap before it
    s = M.perform(got, 99.0, 1000.0, 0.5, CFG, None)["envelopes"]
    assert s[0]["kind"] == "stop" and abs(s[0]["at_ms"] - 1002000.0) < 1.0 and s[0]["dark_ms"] == 1000.0
    # a hushed song's stop dims (its floor), never blacks out
    h = M.perform(M.choose(SONG, "hush", 0.5, CFG), 99.0, 1000.0, 0.5, CFG, "hush")["envelopes"]
    assert h[0]["floor"] == 0.3


def test_an_ending_stays_in_once_reached():
    got = M.choose(SONG, None, 0.5, CFG)
    for pos in (198.0, 205.0, 400.0):
        env = [e for e in M.perform(got, pos, 1000.0, 0.5, CFG, None)["envelopes"] if e["kind"] == "out"]
        assert len(env) == 1
    e = [x for x in M.perform(got, 199.0, 1000.0, 0.5, CFG, None)["envelopes"] if x["kind"] == "out"][0]
    # cold: dark 0.3 s after the last hit, over 0.15 s
    assert abs(e["at_ms"] - (1000.0 + 1.0 + 0.45) * 1000.0) < 1.0 and e["fade_ms"] == 150.0


def test_the_envelopes_stop_and_out():
    stop = {"at_ms": 10000.0, "kind": "stop", "dark_ms": 500.0, "floor": 0.03, "lift": 0.4, "settle_ms": 500.0}
    assert phrasing.envelope(9.0, stop) == (1.0, 1.0)                 # before the gap: as it was
    assert phrasing.envelope(9.7, stop)[0] == 0.03                    # in the gap: down
    assert phrasing.envelope(10.0, stop)[0] > 1.3                     # the return: above steady
    assert phrasing.envelope(11.0, stop) == (1.0, 1.0)                # settled
    out = {"at_ms": 10000.0, "kind": "out", "fade_ms": 1000.0, "floor": 0.0}
    assert phrasing.envelope(8.0, out) == (1.0, 1.0)
    assert abs(phrasing.envelope(9.5, out)[0] - 0.5) < 1e-6           # half way down the fade
    assert phrasing.envelope(10.0, out)[0] == 0.0 and phrasing.envelope(60.0, out)[0] == 0.0   # and stays down
    both = phrasing.envelopes(9.7, None, [stop, out])
    assert abs(both[0] - 0.03 * 0.3) < 1e-6                            # every moment's envelope multiplies


def test_the_real_file_reads():
    cfg = M.load()
    assert set(cfg["kinds"]) >= {"stab", "stop", "end"} and cfg["choose"]["by_idiom"]
    assert M.recipe(cfg, {"kind": "end", "style": "cold"})["envelope"] == "out"
