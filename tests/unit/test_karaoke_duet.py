"""karaoke.duet: who sings which line (routes/karaoke/duet.py)."""
import numpy as np

from routes.karaoke import duet
from routes.karaoke.store import is_duet, song_tags


def _voice(seed, n, spread=0.35):
    rng = np.random.default_rng(seed)
    base = rng.normal(size=64)
    return [{"emb": list(duet._unit(base + spread * np.linalg.norm(base) / 8 * rng.normal(size=64))), "midi": 60.0}
            for _ in range(n)]


def _prior(parts):
    return {"duet": True, "voices": [{"name": "Him", "sex": "man"}, {"name": "Her", "sex": "woman"}],
            "parts": parts, "echoes": {}}


TEXTS = ["the words of this line sung"] * 12


def test_two_clear_voices_where_the_ai_agrees_is_a_duet():
    heard = _voice(1, 6) + _voice(2, 6)
    v = duet.decide(12, _prior(["A"] * 6 + ["B"] * 6), heard, texts=TEXTS)
    assert v["duet"] is True and v["shown"] is True
    assert v["parts"] == ["A"] * 6 + ["B"] * 6


def test_one_voice_is_not_a_duet_whatever_the_ai_says():
    heard = _voice(1, 12)
    v = duet.decide(12, _prior(["A"] * 6 + ["B"] * 6), heard, texts=TEXTS)
    assert v["shown"] is False and v["parts"] == [None] * 12
    assert v["duet"] is True                     # the AI's word still tags it


def test_the_ai_saying_no_is_no():
    v = duet.decide(12, {"duet": False, "voices": [], "parts": [None] * 12, "echoes": {}}, _voice(1, 6) + _voice(2, 6))
    assert v["duet"] is False


def test_each_line_is_the_voice_it_sounds_like_not_the_ai_s_call():
    heard = _voice(1, 6) + _voice(2, 6)
    ai = ["A"] * 6 + ["B"] * 6
    ai[2] = "B"                                  # the AI wrong on one line
    v = duet.decide(12, _prior(ai), heard, texts=list(TEXTS))
    assert v["parts"][2] == "A"                  # bb's eval, 28 Nov: the sound 95% right on solo lines, the AI 69%


def test_both_and_backing_keep_the_ai_word_and_a_is_who_sings_first():
    heard = _voice(1, 6) + [None, None] + _voice(2, 6)
    ai = ["B"] * 6 + ["both", "backing"] + ["A"] * 6
    v = duet.decide(14, _prior(ai), heard, texts=["the words of this line sung"] * 14)
    assert v["duet"] is True
    assert v["parts"][:6] == ["A"] * 6 and v["parts"][6:8] == ["both", "backing"] and v["parts"][8:] == ["B"] * 6
    assert [x["name"] for x in v["voices"]] == ["Her", "Him"]


def test_parse_checks_the_answer():
    got = duet.parse({"duet": True, "voices": [{"name": "Elton John", "sex": "man"}, {"name": "Kiki Dee", "sex": "woman"}],
                      "lines": [{"i": 0, "part": "A"}, {"i": 1, "part": "unknown"}, {"i": 9, "part": "B"},
                                {"i": 2, "part": "both", "echo": "B"}]}, 3)
    assert got["parts"] == ["A", None, "both"] and got["echoes"] == {2: "B"}
    assert duet.parse({"duet": True, "voices": [{"name": "x", "sex": "man"}], "lines": []}, 2)["duet"] is False
    assert duet.parse("nonsense", 2) is None


def test_text_tags_name_a_voice():
    voices = [{"name": "Elton John", "sex": "man"}, {"name": "Kiki Dee", "sex": "woman"}]
    assert duet.text_tag("[Elton:] Don't go breaking my heart", voices) == "A"
    assert duet.text_tag("Kiki: I couldn't if I tried", voices) == "B"
    assert duet.text_tag("(Both) Ooh, honey", voices) == "both"
    assert duet.text_tag("(Hey) Huntrix don't miss", voices) is None


def test_apply_only_for_the_same_lines(tmp_path):
    lines = [{"text": "Ain't no mountain (no no baby)"}, {"text": "high enough"}]
    v = {"duet": True, "shown": True, "voices": [{"name": "M"}, {"name": "T"}], "parts": ["A", "both"],
         "echoes": {0: "B"}}
    duet.save(tmp_path, v, lines, by="test")
    doc = duet.apply({"kind": "synced", "lines": lines}, duet.load(tmp_path))
    assert doc["lines"][0]["voice"] == "A" and doc["lines"][0]["voices"] == ["A", "A", "A", "B", "B", "B"]
    assert doc["lines"][1]["voice"] == "both" and doc["duet"]["voices"][0]["name"] == "M"
    other = duet.apply({"kind": "synced", "lines": [{"text": "other words"}, {"text": "x"}]}, duet.load(tmp_path))
    assert "duet" not in other and "voice" not in other["lines"][0]


def test_the_duets_tag_follows_a_person_over_the_finding():
    assert "Duets" in song_tags({"duet": {"found": True}})
    assert "Duets" not in song_tags({"duet": {"found": True}, "duet_set": False})
    assert is_duet({"duet": {"found": False}, "duet_set": True})
    assert not is_duet({})


def test_a_is_who_sings_first_even_when_that_line_is_not_marked():
    heard = [None] + _voice(1, 6) + _voice(2, 6)
    ai = ["B"] + ["B"] * 6 + ["A"] * 6
    v = duet.decide(13, _prior(ai), heard, texts=["oh"] + ["the words of this line sung"] * 12)
    assert v["parts"][0] is None and v["parts"][1] == "A" and [x["name"] for x in v["voices"]] == ["Her", "Him"]


def test_the_names_follow_the_pitch_when_the_ai_has_them_clearly_swapped():
    heard = _voice(1, 6) + _voice(2, 6)
    for h in heard[:6]:
        h["midi"] = 50.0                         # the first voice sings an octave under the second
    prior = _prior(["A"] * 6 + ["B"] * 6)
    prior["voices"] = [{"name": "Diana", "sex": "woman"}, {"name": "Lionel", "sex": "man"}]
    v = duet.decide(12, prior, heard, texts=TEXTS)
    assert [x["name"] for x in v["voices"]] == ["Lionel", "Diana"] and v["parts"][0] == "A"
