"""Gallery: art-tag matching to the room's music (owner, 27 Oct: in Ambient, jazz -> abstract / painterly / nocturnal,
lyrical classical -> landscapes, seascapes, still life, pop -> urban, fun, peopled), the art director's wish (owner,
28 Oct: room.direction ``art`` -> direction_effective -> music_dir -> the gallery's weights) and the cluster hold per
song.  Pure Python: synthetic decks, the decode worker's index on a temp bucket (no GPU, no images decoded)."""
from __future__ import annotations

import collections
import json
import pathlib
import random

import pytest

from stream_service.plugins.sources import gallery as g

from .test_sessions import client  # noqa: F401 (fixture)

REPO = pathlib.Path(__file__).resolve().parents[2]

JAZZ = {"genres": {"jazz": 0.8, "blues": 0.2}, "genre": "jazz",
        "moods": ["mellow", "elegant", "warm", "organic"], "valence": 0.45, "organic": 0.8, "energy_slow": 0.35}
CLASSICAL = {"genres": {"classical": 0.7, "piano": 0.3}, "genre": "classical", "moods": ["calm", "dreamy", "elegant"],
             "valence": 0.55, "organic": 0.9, "energy_slow": 0.25}
POP = {"genres": {"pop": 0.7, "indie": 0.3}, "genre": "pop", "moods": ["bright", "playful", "warm"],
       "valence": 0.8, "organic": 0.5, "energy_slow": 0.65}

# (subject, moods, music groups, energy, valence, organic): the tagger's calibration examples (config/art_tags.yaml)
KINDS = {
    "abstract": (["abstract"], ["dark", "cool"],
                 {"jazz": 1.0, "classical": 0.6, "ambient": 0.6, "pop": 0.3, "techno": 0.2, "soul": 0.5},
                 0.4, 0.4, 0.7),
    "landscape": (["landscape"], ["calm", "dreamy"],
                  {"classical": 1.0, "ambient": 0.9, "jazz": 0.4, "pop": 0.3, "techno": 0.05}, 0.2, 0.6, 0.9),
    "still-life": (["still-life"], ["elegant", "warm"],
                   {"classical": 0.9, "jazz": 0.6, "ambient": 0.6, "pop": 0.3, "techno": 0.05}, 0.2, 0.55, 0.85),
    "urban": (["urban", "people"], ["bright", "playful"],
              {"pop": 1.0, "soul": 0.8, "world": 0.7, "house": 0.6, "classical": 0.2, "jazz": 0.3}, 0.7, 0.85, 0.5),
    "neon": (["pattern"], ["hypnotic", "geometric"],
             {"techno": 1.0, "house": 0.8, "pop": 0.4, "classical": 0.05, "jazz": 0.2}, 0.8, 0.6, 0.1),
}


def raw(kind: str, quality: float = 0.7, auto_ok: bool = True, **over) -> dict:
    subj, moods, music, e, v, o = KINDS[kind]
    ai = {"subject": subj, "medium": "painting", "moods": moods, "palette": "cool", "energy": e, "valence": v,
          "organic": o, "density": 0.4, "quality": quality, "music": music, "auto_ok": auto_ok, "confidence": 0.8}
    ai.update(over)
    return {"tags": [*subj, "painting", *moods], "ai": ai, "added": [], "removed": []}


def name(i: int, day: str = "20261020") -> str:
    return f"{day}-120000-{i:08x}.jpg"


def deck(entries: dict, favs: tuple = (), seed: int = 1) -> g._Deck:
    d = g._Deck("north-screen")
    d.rng = random.Random(seed)
    tags = {n: r for n, r in entries.items() if r is not None}
    d.set_index([n for n in entries if n in favs], [n for n in entries if n not in favs], tags, "sig")
    return d


def library(per: int = 10) -> tuple[dict, dict]:
    entries, kind_of = {}, {}
    i = 0
    for k in KINDS:
        for _ in range(per):
            n = name(i)
            entries[n], kind_of[n] = raw(k), k
            i += 1
    return entries, kind_of


def first_picks(entries: dict, music: dict | None, trials: int = 300, **kw) -> collections.Counter:
    """What the first pick is over many fresh decks (no shown-memory in the way)."""
    _, kind_of = library()
    out: collections.Counter = collections.Counter()
    for s in range(trials):
        d = deck(entries, seed=s, **kw)
        n = d.draw("library", "music", music)
        out[kind_of.get(n, n)] += 1
    return out


def test_group_words_match_tagpass():
    from stream_service.tools import tagpass
    assert g.GROUP_WORDS == tagpass.GROUP_WORDS


def test_facets_follow_the_contract():
    r = raw("landscape")
    r["removed"] = ["dreamy"]
    r["added"] = ["seascape", "icy", "my-holiday"]
    f = g.facets(r)
    assert f["subject"] == {"landscape", "seascape"}           # an added lexicon subject joins; free words don't
    assert f["moods"] == {"calm", "icy"}                        # removed drops the AI's word
    assert f["genre_fit"]["classical"] == 1.0 and f["genre_fit"]["piano"] == 1.0 and f["genre_fit"]["jazz"] == 0.4
    assert f["genre_fit"]["electronic"] == 0.05                 # a word takes the best of its groups
    assert g.facets(None) is None
    hand = g.facets({"tags": ["urban"], "ai": {}, "added": ["urban"], "removed": []})
    assert hand["subject"] == {"urban"} and not hand["ai"]      # a person's tags only: neutral score, still continuity


@pytest.mark.parametrize("music,want", [(JAZZ, {"abstract"}), (CLASSICAL, {"landscape", "still-life"}),
                                        (POP, {"urban"})])
def test_weights_rank_the_fitting_art_first(music, want):
    entries, kind_of = library(per=1)
    ws = {kind_of[n]: g.music_weight(g.facets(r), music, False) for n, r in entries.items()}
    top = sorted(ws, key=ws.get, reverse=True)[:len(want)]
    assert set(top) == want, ws


@pytest.mark.parametrize("music,want", [(JAZZ, ("abstract",)), (CLASSICAL, ("landscape", "still-life")),
                                        (POP, ("urban",))])
def test_draws_follow_the_music(music, want):
    entries, _ = library()
    c = first_picks(entries, music)
    assert sum(c[k] for k in want) > 0.6 * sum(c.values()), c
    assert c.most_common(1)[0][0] in want


def test_the_cluster_moves_when_the_music_does():
    entries, kind_of = library()
    d = deck(entries)
    jazz = collections.Counter(kind_of[d.draw("library", "music", dict(JAZZ, track=1))] for _ in range(12))
    pop = collections.Counter(kind_of[d.draw("library", "music", dict(POP, track=2))] for _ in range(12))   # a new song
    assert jazz.most_common(1)[0][0] == "abstract" and pop.most_common(1)[0][0] == "urban", (jazz, pop)


def test_continuity_favours_the_same_subject():
    entries = {}
    for i in range(5):
        entries[name(i)] = raw("landscape")
        entries[name(100 + i)] = raw("landscape", subject=["seascape"], moods=["icy"])
    same = 0
    for s in range(300):
        d = deck(entries, seed=s)
        d.shown.append(name(0))                                 # a landscape was just shown
        same += d.draw("library", "music", None) in {name(i) for i in range(5)}
    # base weights equal; 4 landscapes x (1.6 x 1.2)^2 against 5 seascapes: about 0.75
    assert same / 300 > 0.6


def test_no_music_still_draws_and_prefers_quality_and_favourites():
    entries = {name(i): raw("landscape", quality=0.9 if i < 5 else 0.2) for i in range(10)}
    c = collections.Counter()
    for s in range(300):
        c[int(deck(entries, seed=s).draw("library", "music", None)[-12:-4], 16) < 5] += 1
    assert c[True] > 0.75 * 300
    fav = deck(entries, favs=(name(9),))
    assert fav.wbase == {} and g.music_weight(fav.facets[name(9)], None, True) > \
        g.music_weight(fav.facets[name(8)], None, False)


def test_untagged_are_neutral_and_never_blank():
    entries = {name(i): None for i in range(6)}
    d = deck(entries)
    picks = {d.draw("library", "music", JAZZ) for _ in range(6)}
    assert picks == set(entries)                                # all eligible, no repeats within half the pool
    assert g.music_weight(None, JAZZ, False) == pytest.approx(0.575 * g.UNTAGGED)   # a middling picture, neutral fit
    assert g.music_weight(None, None, False) == pytest.approx(0.575)                 # no music: as a middling one


def test_auto_ok_false_is_never_picked_unless_a_favourite():
    entries = {name(i): raw("abstract") for i in range(6)}
    entries[name(50)] = raw("abstract", quality=1.0, auto_ok=False)
    d = deck(entries)
    for _ in range(200):
        assert d.draw("library", "music", JAZZ) != name(50)
        assert d.draw("library", "off") != name(50)             # the plain pools skip it too
        assert d.draw("mixed", "off") != name(50)
    fav = deck(entries, favs=(name(50),))
    assert any(fav.draw("library", "music", JAZZ) == name(50) for _ in range(200))


def test_every_weight_zero_falls_back_to_the_plain_pool():
    entries = {name(i): raw("abstract", auto_ok=False) for i in range(4)}
    d = deck(entries)
    assert d.draw("library", "music", JAZZ) in entries          # never a blank screen


def test_library_has_old_favourites_and_dated_stills_mixed_does_not():
    old_fav, old, new = name(1, "20250101"), name(2, "20250102"), name(3, "20261026")
    d = deck({old_fav: None, old: None, new: None}, favs=(old_fav,))
    cats = d._cats()
    assert set(cats["lib"]) == {old_fav, old, new}
    assert old not in cats["fav"] + cats["r7"] + cats["r30"]
    seen = {d.draw("library") for _ in range(9)}
    assert seen == {old_fav, old, new}


def test_weights_are_cached_until_the_music_or_index_changes(monkeypatch):
    entries, _ = library(per=2)
    d = deck(entries)
    calls = []
    real = g.music_weight
    monkeypatch.setattr(g, "music_weight", lambda *a: calls.append(1) or real(*a))
    d.draw("library", "music", JAZZ)
    n = len(calls)
    d.draw("library", "music", dict(JAZZ, energy_slow=0.32))    # rounds to the same key
    assert len(calls) == n
    d.draw("library", "music", dict(POP, track=1))              # a new song: its music is taken at once
    assert len(calls) == 2 * n


def test_shown_memory_is_larger_for_the_library():
    assert g._Deck._mem("library", 600) == 60 and g._Deck._mem("library", 30) == 15
    assert g._Deck._mem("favourites", 600) == 24


def test_an_older_deck_is_upgraded_in_place():
    d = g._Deck.__new__(g._Deck)
    d.__dict__.update(screen="north-screen", lock=__import__("threading").Lock(), fav=[], seq=[name(1)], bad=set(),
                      bags={}, shown=collections.deque([name(1)], maxlen=24), t_index=0.0, indexed=True,
                      rng=random.Random(1))
    d.upgrade()
    assert d.shown.maxlen == g.SHOWN_MAX and list(d.shown) == [name(1)] and d.facets == {}
    assert d.draw("library", "music", JAZZ) == name(1)


def test_ladder_param_sets_bars_per_change():
    gal = g.Gallery.__new__(g.Gallery)
    gal.params0, gal.rung = {"ladder": "16-8-4"}, None
    assert gal._unit("bar", 0.2, 4, 4) == 64
    assert gal._unit("bar", 0.57, 4, 4) == 64                   # hysteresis: still calm just over the edge
    assert gal._unit("bar", 0.7, 4, 4) == 32
    assert gal._unit("bar", 0.9, 4, 4) == 16
    gal.params0, gal.rung = {}, None
    assert gal._unit("bar", 0.2, 4, 4) == 16                    # the default ladder is unchanged (4-2-1)


def test_worker_index_reads_tags_never_the_prompt(tmp_path):
    a, b, c = name(1), name(2), name(3)
    (tmp_path / "bucket.json").write_text(json.dumps({"sequence": [{"file": a}, {"file": b}, {"file": c}],
                                                      "favorites": [a, "tmpx.jpg"]}))
    for n in (a, b, c):
        (tmp_path / n).write_bytes(b"")
    secret = "a secret prompt about a misty lake"
    side = {"prompt": secret, "workflow": "flux", **{k: v for k, v in raw("landscape").items() if k == "tags"}}
    side["tagging"] = {"v": 1, "status": "done", "ai": {**raw("landscape")["ai"], "look": secret},
                       "added": ["seascape"], "removed": []}
    (tmp_path / f"{a}.json").write_text(json.dumps(side))
    (tmp_path / f"{b}.json").write_text(json.dumps({"prompt": secret, "workflow": "flux"}))   # untagged
    meta = g.index_once(tmp_path)
    assert meta["ok"] and meta["fav"] == [a] and meta["seq"] == [a, b, c]
    assert set(meta["tags"]) == {a} and meta["tags"][a]["added"] == ["seascape"]
    assert secret not in json.dumps(meta)                       # neither the prompt nor the AI's look
    again = g.index_once(tmp_path, tsig=meta["tsig"])
    assert "tags" not in again and again["tsig"] == meta["tsig"]  # unchanged: the tags aren't resent
    d = g._Deck("north-screen")
    d.apply_index(meta)
    assert d.facets[a]["subject"] == {"landscape", "seascape"}
    d.apply_index(again)                                        # no tags in the reply: the last ones are kept
    assert a in d.facets


AMBIENT_LOOKS = ("gallery-ambient-ghosts", "gallery-ambient-contact-sheet", "gallery-ambient-show-colours",
                 "gallery-ambient-little-planets", "gallery-ambient-kaleidoscope")


def test_repo_presets_make_ambient_the_matched_gallery():
    from stream_service.plugins import default_registry
    from stream_service.presets import Presets
    p = Presets(REPO / "config" / "live", default_registry())
    for n in ("gallery-ambient", "gallery-ambient-lively", *AMBIENT_LOOKS):
        s = p.scenes[n]
        assert s["params"]["pool"] == "library" and s["params"]["match"] == "music", n
        assert "ambient" in s["tags"]["modes"], n
    for n in AMBIENT_LOOKS:                                     # each keeps its look's effect
        assert p.scenes[n]["effects"] == p.scenes[n.replace("gallery-ambient-", "gallery-")]["effects"], n
    assert p.scenes["gallery-ambient"]["params"]["ladder"] == "16-8-4"
    assert "ambient" not in (p.scenes["live-art"].get("tags") or {}).get("modes", [])


# -- the art director's wish (owner, 28 Oct: the connective tissue between tagging and Ambient) ---------------------
def art_table() -> dict:
    import yaml
    return yaml.safe_load((REPO / "config" / "art_tags.yaml").read_text())["music_art"]


def wished(music: dict, group: str) -> dict:
    a = art_table()[group]
    return dict(music, art={k: list(a.get(k) or []) for k in ("subjects", "moods", "media", "avoid")})


def test_music_art_words_are_lexicon_words():
    import yaml
    lex = yaml.safe_load((REPO / "config" / "art_tags.yaml").read_text())["facets"]
    for group, a in art_table().items():
        assert set(a["subjects"]) | set(a["avoid"]) <= set(lex["subject"]), group
        assert set(a["moods"]) <= set(lex["moods"]) and set(a["media"]) <= set(lex["medium"]), group


def test_the_wish_leads_over_a_flat_genre_fit():
    # every picture fits jazz equally (the tagger can't tell them apart): the wish alone sorts them
    flat = {"jazz": 0.8, "classical": 0.8, "pop": 0.8}
    ws = {k: g.music_weight(g.facets(raw(k, music=flat)), wished(JAZZ, "jazz"), False) for k in KINDS}
    assert max(ws, key=ws.get) == "abstract", ws
    no_wish = {k: g.music_weight(g.facets(raw(k, music=flat)), JAZZ, False) for k in KINDS}
    assert ws["abstract"] / ws["landscape"] > 2.0 * no_wish["abstract"] / no_wish["landscape"]


def test_wish_factor_subject_mood_medium_and_avoid():
    f = g.facets(raw("abstract"))                                # abstract, painting, dark + cool
    assert f["medium"] == {"painting"}
    w = lambda **a: g.art_factor(f, g.art_wish({"art": a}))      # noqa: E731
    assert w(subjects=["abstract"]) == 1.0
    assert w(subjects=["urban", "abstract"]) == pytest.approx(1.0 - g.ART_STEP)    # a later subject: a little less
    assert w(subjects=["urban"]) == g.ART_SUBJECT_MISS
    assert w(moods=["dark"]) == 1.0 and w(moods=["bright"]) == g.ART_MOOD_MISS
    assert w(media=["painting"]) == 1.0 and w(media=["photograph"]) == g.ART_MEDIUM_MISS
    assert w(media=[], avoid=["people"]) == 1.0                  # a facet the wish leaves empty: no factor
    assert w(subjects=["abstract"], avoid=["abstract"]) == pytest.approx(g.ART_AVOID)
    assert g.art_wish({"art": {"subjects": [], "moods": [], "media": [], "avoid": [], "theme": "x"}}) is None
    assert g.art_factor(g.facets({"tags": [], "ai": {}, "added": [], "removed": []}),
                        g.art_wish({"art": {"subjects": ["urban"]}})) == 1.0              # nothing known: neutral
    assert g.music_weight(None, wished(JAZZ, "jazz"), False) == g.music_weight(None, JAZZ, False)   # untagged


def test_avoided_subjects_stay_off_the_wall():
    entries = {name(i): raw("abstract") for i in range(10)}
    entries.update({name(100 + i): raw("abstract", subject=["animals"], quality=1.0) for i in range(10)})
    jazz = wished(JAZZ, "jazz")
    assert "animals" in jazz["art"]["avoid"]
    animals = sum(int(deck(entries, seed=s).draw("library", "music", jazz)[-12:-4], 16) >= 100 for s in range(200))
    assert animals < 0.1 * 200, animals


@pytest.mark.parametrize("music,group,want", [(JAZZ, "jazz", ("abstract",)),
                                              (CLASSICAL, "classical", ("landscape", "still-life")),
                                              (POP, "pop", ("urban",))])
def test_draws_follow_the_wish(music, group, want):
    entries, _ = library()
    c = first_picks(entries, wished(music, group))
    assert sum(c[k] for k in want) > 0.6 * sum(c.values()), c


def test_no_wish_is_the_weight_as_before():
    entries, _ = library(per=1)
    for r in entries.values():
        f = g.facets(r)
        for m in (JAZZ, POP, None):
            base = g.music_weight(f, m, False)
            if m is not None:
                for art in (None, {}, {"subjects": [], "moods": [], "media": [], "avoid": []}, "junk"):
                    assert g.music_weight(f, dict(m, art=art), False) == base
    assert g.music_key(JAZZ) == g.music_key(dict(JAZZ, art=None))


def test_cluster_holds_until_a_new_song_or_a_real_move_after_a_minute():
    d = g._Deck("north-screen")
    jazz, pop = dict(JAZZ, track=1), dict(POP, track=1)
    assert d._held(jazz, now=0.0) is jazz
    assert d._held(dict(jazz, energy_slow=0.6), now=5.0) is jazz          # the listener re-reads: held
    assert d._held(pop, now=30.0) is jazz                                 # even a new genre, inside the minute ...
    assert d._held(pop, now=61.0) is pop                                  # ... until it has passed
    assert d._held(dict(pop, valence=0.85), now=200.0) is pop             # a small move never re-weights
    new = dict(JAZZ, track=2)
    assert d._held(new, now=201.0) is new                                 # a new song: at once
    later = dict(new, genres={"blues": 1.0}, genre="blues")
    assert d._held(later, now=215.0) is later                             # the listener's take on it, soon after
    assert d._held(dict(later, genres={"soul": 1.0}), now=240.0) is later  # then held again
    d2 = g._Deck("north-screen")
    assert d2._held(None, now=0.0) is None and d2._held(jazz, now=1.0) is jazz   # music arriving: at once


def test_continuity_still_shapes_runs_under_a_wish():
    entries = {}
    for i in range(5):
        entries[name(i)] = raw("abstract")
        entries[name(100 + i)] = raw("abstract", subject=["interior"], moods=["warm"])
    jazz = wished(JAZZ, "jazz")
    same = 0
    for s in range(300):
        d = deck(entries, seed=s)
        d.shown.append(name(100))                               # an interior was just shown
        same += d.draw("library", "music", jazz) in {name(100 + i) for i in range(1, 5)}
    assert same / 300 > 0.3


# -- the wish's path to the gallery: room.direction -> the proposal -> direction_effective ---------------------------
def test_art_wish_is_plain_data_shape_checked():
    from stream_service import music_direction as M
    a = M.art_wish({"subjects": ["Abstract", "still life", 3, "urban", "people", "sky", "x"], "moods": ["dark"],
                    "media": ["painting"], "avoid": ["animals"], "theme": "late-night city, smoky rooms",
                    "via": "listener", "extra": 1})
    assert a == {"subjects": ["abstract", "still-life", "urban", "people", "sky"], "moods": ["dark"],
                 "media": ["painting"], "avoid": ["animals"], "theme": "late-night city, smoky rooms",
                 "via": "listener"}
    for bad in (None, "jazz", [], {}, {"subjects": "abstract"}, {"via": "none", "subjects": []}):
        assert M.art_wish(bad) is None


def test_room_wish_reaches_the_effective_direction():
    from stream_service import music_direction as M
    from stream_service.director import AUTO_MUSIC_DEFAULTS, validate_direction
    from .test_music_direction import room_dir
    wish = M.art_wish({"subjects": ["abstract"], "moods": ["dark"], "via": "genre"})
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))

    def rd(art):
        r = room_dir()
        if art is not None:
            r["direction"]["art"] = art
        return r
    for t in range(40):
        md.update(t / 10, None, False, (), True, room=rd(wish))
    panel = validate_direction({})
    assert md.proposal["art"] == wish and M.effective_direction(panel, md.proposal, md.m)["art"] == wish
    assert "art" in M.music_fields(panel, md.proposal, md.m)
    other = dict(wish, subjects=["urban"])
    assert md.update(4.1, None, False, (), True, room=rd(other)) == "music" and md.proposal["art"] == other
    assert md.update(4.2, None, False, (), True, room=rd(None)) == "music" and "art" not in md.proposal
    assert "art" not in M.effective_direction(panel, md.proposal, md.m)     # no wish: the direction as before
    assert "art" not in M.effective_direction(panel, md.proposal, 0.25)


async def test_session_puts_the_wish_and_the_song_in_music_dir(client):  # noqa: F811 (the fixture)
    import time
    from .test_cue import AUTO, live
    sid, s, eng = await live(client, auto=AUTO)
    mgr = s.mgr
    data = {"energy": 0.4, "pace": 0.4, "moods": ["mellow"], "genres": {"jazz": 1.0},
            "art": {"subjects": ["abstract", "interior"], "moods": ["dark"], "media": [], "avoid": ["animals"],
                    "theme": "smoky rooms", "via": "genre"}}
    t0 = time.monotonic()
    for i in range(50):
        mgr.on_room_message({"type": "room.direction", "data": data})
        mgr.room_direction["t"] = t0 + i / 10
        s.tick(t0 + i / 10)
    md = s.runtime.extra["music_dir"]
    assert md["art"]["subjects"] == ["abstract", "interior"] and md["art"]["theme"] == "smoky rooms", md
    n = md["track"]
    mgr.on_room_message({"type": "room.event", "data": {"kind": "track_change", "id": "track_change:7",
                                                        "at_ms": 1.0, "track_id": 7}})
    mgr.on_room_message({"type": "room.event", "data": {"kind": "track_change", "id": "track_change:7",
                                                        "at_ms": 1.0, "track_id": 7, "detail": "update"}})
    s.tick(t0 + 5.1)
    assert s.runtime.extra["music_dir"]["track"] == n + 1             # one song, however often it is re-sent
    mgr.on_room_message({"type": "room.direction", "data": {k: v for k, v in data.items() if k != "art"}})
    assert "art" not in mgr.room_direction["direction"]                # an older show_service: no wish, as before


@pytest.mark.parametrize("music,want", [
    ({"energy": 0.38, "moods": ["mellow", "elegant", "warm", "organic"], "genres": {"jazz": 1.0}, "valence": 0.45,
      "organic": 0.8, "density": 0.4}, {"gallery-ambient-contact-sheet", "gallery-ambient-ghosts", "gallery-ambient"}),
    ({"energy": 0.65, "moods": ["bright", "playful", "geometric", "hypnotic"], "genres": {"pop": 1.0},
      "valence": 0.8, "organic": 0.45, "density": 0.6}, {"gallery-ambient-little-planets", "gallery-ambient-lively"}),
    ({"energy": 0.85, "moods": ["euphoric", "hypnotic", "bright", "aggressive"], "genres": {"techno": 1.0},
      "valence": 0.5, "organic": 0.1, "density": 0.8}, {"gallery-ambient-kaleidoscope"}),
])
def test_the_director_picks_a_fitting_ambient_look(music, want):
    from stream_service.director import STYLE_POWER
    from stream_service.plugins import default_registry
    from stream_service.presets import Presets
    from stream_service.tags import scene_weight
    p = Presets(REPO / "config" / "live", default_registry())
    pool = [n for n, s in p.scenes.items() if "ambient" in ((s.get("tags") or {}).get("modes") or [])]
    assert set(AMBIENT_LOOKS) <= set(pool)
    ws = {n: scene_weight(p.scenes[n]["tags"], music) ** STYLE_POWER for n in pool}
    assert sum(ws[n] for n in want) > 0.8 * sum(ws.values()), ws
