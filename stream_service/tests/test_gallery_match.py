"""Gallery: art-tag matching to the room's music (owner, 27 Oct: in Ambient, jazz -> abstract / painterly / nocturnal,
lyrical classical -> landscapes, seascapes, still life, pop -> urban, fun, peopled).  Pure Python: synthetic decks,
the decode worker's index on a temp bucket (no GPU, no images decoded)."""
from __future__ import annotations

import collections
import json
import pathlib
import random

import pytest

from stream_service.plugins.sources import gallery as g

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
    jazz = collections.Counter(kind_of[d.draw("library", "music", JAZZ)] for _ in range(12))
    pop = collections.Counter(kind_of[d.draw("library", "music", POP)] for _ in range(12))
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
    d.draw("library", "music", POP)
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


def test_repo_presets_make_ambient_the_matched_gallery():
    from stream_service.plugins import default_registry
    from stream_service.presets import Presets
    p = Presets(REPO / "config" / "live", default_registry())
    for n in ("gallery-ambient", "gallery-ambient-lively"):
        s = p.scenes[n]
        assert s["params"]["pool"] == "library" and s["params"]["match"] == "music", n
        assert "ambient" in s["tags"]["modes"], n
    assert p.scenes["gallery-ambient"]["params"]["ladder"] == "16-8-4"
    assert "ambient" not in (p.scenes["live-art"].get("tags") or {}).get("modes", [])
